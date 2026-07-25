/**
 * Stateful, streaming Tick-Run-Bar builder.
 *
 * `./bars`'s `constructBars` aggregates a whole tape at once. A live tape needs a
 * builder that accepts one trade at a time and emits a bar the instant
 * `max(buyTicks, sellTicks) >= threshold`.
 *
 * Because this bar type is *adaptive*, the builder also **exposes its state**:
 * `buyTicks`, `sellTicks`, `dominantCount`, `threshold` (the frozen snapshot the
 * open bar is judged against), `expectedTicks`, `expectedBuyProbability`, and
 * `tickSign`.
 */

import {
  REQUIRED_TRADE_FIELDS,
  TickRunBarsValidationError,
  finite,
  rounded,
  timestampMs,
  validateConfig,
  type Bar,
  type CloseReason,
  type Config,
  type DominantSide,
  type Trade,
} from "./bars.ts";

export class StreamingTickRunBarBuilder {
  private readonly closePartial: boolean;
  private readonly initialSign: number;
  private readonly initialExpectedTicks: number;
  private readonly initialBuyProbability: number;
  private readonly alphaTicks: number;
  private readonly alphaProbability: number;
  private readonly floor: number;
  private readonly multiplier: number;

  // validation state
  private ids = new Set<string>();
  private closedSessions = new Set<string>();
  private validationSession: string | null = null;
  private priorTime: number | null = null;
  private priorSequence: number | null = null;
  private symbol: string | null = null;
  private currency: string | null = null;

  // adaptive + aggregation state
  private current: Trade[] = [];
  private session: string | null = null;
  private previousPrice: number | null = null;
  private _tickSign: number;
  private _expectedTicks: number;
  private _expectedBuyProbability: number;
  private _buyTicks = 0;
  private _sellTicks = 0;
  private _threshold = 0;
  private frozenExpectedTicks = 0;
  private frozenBuyProbability = 0;
  private barCount = 0;
  private flushed = false;

  constructor(config: Config) {
    validateConfig(config);
    this.closePartial = config.closePartial;
    this.initialSign = config.initialTickSign;
    this.initialExpectedTicks = config.initialExpectedTicks;
    this.initialBuyProbability = config.initialBuyProbability;
    this.alphaTicks = config.alphaTicks;
    this.alphaProbability = config.alphaBuyProbability;
    this.floor = config.thresholdFloorTicks;
    this.multiplier = config.thresholdMultiplier;
    this._tickSign = this.initialSign;
    this._expectedTicks = this.initialExpectedTicks;
    this._expectedBuyProbability = this.initialBuyProbability;
    this.beginBar();
  }

  /** Cumulative `+1` signs in the open bar. */
  get buyTicks(): number {
    return this._buyTicks;
  }

  /** Cumulative `-1` signs in the open bar. */
  get sellTicks(): number {
    return this._sellTicks;
  }

  /** The run length being tested: `max(buyTicks, sellTicks)`. */
  get dominantCount(): number {
    return Math.max(this._buyTicks, this._sellTicks);
  }

  /** Frozen threshold (ticks) the open bar is being judged against. */
  get threshold(): number {
    return this._threshold;
  }

  /** Live EWMA estimate of ticks per bar (feeds the *next* bar's threshold). */
  get expectedTicks(): number {
    return this._expectedTicks;
  }

  /** Live EWMA estimate of the buy-tick probability. */
  get expectedBuyProbability(): number {
    return this._expectedBuyProbability;
  }

  /** Sign the next flat trade would carry (+1 or -1). */
  get tickSign(): number {
    return this._tickSign;
  }

  /** Trades currently held in the open bar. */
  get tickCount(): number {
    return this.current.length;
  }

  private beginBar(): void {
    this.current = [];
    this._buyTicks = 0;
    this._sellTicks = 0;
    this.frozenExpectedTicks = this._expectedTicks;
    this.frozenBuyProbability = this._expectedBuyProbability;
    this._threshold = Math.max(
      this.floor,
      this.multiplier *
        this.frozenExpectedTicks *
        Math.max(this.frozenBuyProbability, 1 - this.frozenBuyProbability),
    );
  }

  private validateTrade(raw: unknown): Trade {
    if (!raw || typeof raw !== "object") {
      throw new TickRunBarsValidationError("each trade must be an object");
    }
    const trade = raw as Trade;
    const missing = REQUIRED_TRADE_FIELDS.filter((field) => !(field in trade));
    if (missing.length) {
      throw new TickRunBarsValidationError(`trade is missing: ${missing.join(", ")}`);
    }
    for (const field of ["tradeId", "session", "symbol", "currency"] as const) {
      if (typeof trade[field] !== "string" || !trade[field].trim()) {
        throw new TickRunBarsValidationError(`${field} must be a non-empty string`);
      }
    }
    if (this.ids.has(trade.tradeId)) throw new TickRunBarsValidationError("tradeId must be unique");
    this.ids.add(trade.tradeId);

    const currentTime = timestampMs(trade.timestamp);
    if (this.priorTime !== null && currentTime < this.priorTime) {
      throw new TickRunBarsValidationError("trades must be chronological");
    }
    if (this.priorTime !== null && currentTime === this.priorTime) {
      const sequence = trade.sequence;
      if (
        !Number.isInteger(sequence) ||
        this.priorSequence === null ||
        (sequence as number) <= this.priorSequence
      ) {
        throw new TickRunBarsValidationError(
          "equal timestamps require strictly increasing integer sequence values",
        );
      }
    }
    this.priorSequence = Number.isInteger(trade.sequence) ? (trade.sequence as number) : null;
    this.priorTime = currentTime;

    const price = finite(trade.price, "price");
    const volume = finite(trade.volume, "volume");
    if (price <= 0 || volume <= 0) {
      throw new TickRunBarsValidationError("price and volume must be positive");
    }

    if (this.symbol === null) {
      this.symbol = trade.symbol;
      this.currency = trade.currency;
    } else if (trade.symbol !== this.symbol || trade.currency !== this.currency) {
      throw new TickRunBarsValidationError("one symbol and one currency are allowed per call");
    }

    if (this.validationSession === null) this.validationSession = trade.session;
    else if (trade.session !== this.validationSession) {
      this.closedSessions.add(this.validationSession);
      if (this.closedSessions.has(trade.session)) {
        throw new TickRunBarsValidationError("a session may not reappear after another session begins");
      }
      this.validationSession = trade.session;
    }
    return trade;
  }

  private emit(reason: CloseReason): Bar | null {
    if (!this.current.length) return null;
    const prices = this.current.map((t) => t.price);
    const volumes = this.current.map((t) => t.volume);
    const dominantCount = Math.max(this._buyTicks, this._sellTicks);
    const dominantSide: DominantSide =
      this._buyTicks > this._sellTicks ? "buy" : this._sellTicks > this._buyTicks ? "sell" : "tie";
    const isComplete = reason === "threshold";
    const bar: Bar = {
      barIndex: this.barCount,
      session: this.current[0].session,
      startTime: this.current[0].timestamp,
      endTime: this.current.at(-1)!.timestamp,
      lastTradeTime: this.current.at(-1)!.timestamp,
      open: rounded(prices[0]),
      high: rounded(Math.max(...prices)),
      low: rounded(Math.min(...prices)),
      close: rounded(prices.at(-1)!),
      volume: rounded(volumes.reduce((a, b) => a + b, 0)),
      dollarValue: rounded(this.current.reduce((a, t) => a + t.price * t.volume, 0)),
      tickCount: this.current.length,
      firstTradeId: this.current[0].tradeId,
      lastTradeId: this.current.at(-1)!.tradeId,
      closeReason: reason,
      buyTicks: this._buyTicks,
      sellTicks: this._sellTicks,
      dominantCount,
      dominantSide,
      thresholdTicks: rounded(this._threshold),
      overshootTicks: isComplete ? rounded(Math.max(0, dominantCount - this._threshold)) : 0,
      thresholdMet: isComplete,
      isComplete,
      frozenExpectedTicks: rounded(this.frozenExpectedTicks),
      frozenBuyProbability: rounded(this.frozenBuyProbability),
    };
    this.barCount += 1;
    if (isComplete) {
      const observedTicks = this.current.length;
      const observedBuyProbability = this._buyTicks / observedTicks;
      this._expectedTicks = (1 - this.alphaTicks) * this._expectedTicks + this.alphaTicks * observedTicks;
      this._expectedBuyProbability =
        (1 - this.alphaProbability) * this._expectedBuyProbability +
        this.alphaProbability * observedBuyProbability;
    }
    this.beginBar();
    return bar;
  }

  /** Accept one trade and return any bars it closes (zero or one). */
  push(rawTrade: Trade): Bar[] {
    if (this.flushed) throw new TickRunBarsValidationError("cannot push after flush()");
    const trade = this.validateTrade(rawTrade);
    const emitted: Bar[] = [];

    if (this.session !== null && trade.session !== this.session) {
      if (this.current.length && this.closePartial) {
        const bar = this.emit("session_end");
        if (bar) emitted.push(bar);
      } else {
        this.beginBar();
      }
      this.previousPrice = null;
      this._tickSign = this.initialSign;
      this._expectedTicks = this.initialExpectedTicks;
      this._expectedBuyProbability = this.initialBuyProbability;
      this.beginBar();
    }
    this.session = trade.session;

    if (this.previousPrice !== null) {
      this._tickSign =
        trade.price > this.previousPrice ? 1 : trade.price < this.previousPrice ? -1 : this._tickSign;
    }
    this.previousPrice = trade.price;
    this.current.push(trade);
    if (this._tickSign === 1) this._buyTicks += 1;
    if (this._tickSign === -1) this._sellTicks += 1;

    if (Math.max(this._buyTicks, this._sellTicks) >= this._threshold) {
      const bar = this.emit("threshold");
      if (bar) emitted.push(bar);
    }
    return emitted;
  }

  /** Feed an array of trades, returning every bar closed along the way. */
  pushMany(trades: readonly Trade[]): Bar[] {
    const emitted: Bar[] = [];
    for (const trade of trades) emitted.push(...this.push(trade));
    return emitted;
  }

  /** Close the final partial bar (if `closePartial`) and end the stream. */
  flush(): Bar[] {
    this.flushed = true;
    if (this.current.length && this.closePartial) {
      const bar = this.emit("stream_end");
      return bar ? [bar] : [];
    }
    return [];
  }
}
