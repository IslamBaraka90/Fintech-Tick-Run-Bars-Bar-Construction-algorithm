/**
 * Causal Tick-Run Bars under an explicit, disclosed EWMA convention.
 *
 * A faithful, cross-language twin of the Python `fintech_tick_run_bars.core`
 * module and of the reference algorithm published at The Fintech Builder (topic
 * `D01-F01-A07`).
 *
 * **What "run" means here.** A run is the **larger cumulative count** of positive
 * and negative tick-rule signs in the open bar — *not* the longest consecutive
 * same-sign streak:
 *
 *     close the bar when  max(buyTicks, sellTicks) >= threshold
 *
 * `+ + - +` has a longest consecutive streak of 2 but a dominant cumulative count
 * of 3; this package uses the cumulative reading.
 *
 * The threshold adapts through two EWMAs (updated only after a complete bar) and
 * is driven by the dominant-side probability `max(p, 1 - p)`, which keeps it
 * symmetric between buy- and sell-skewed tapes. It is frozen at bar open and
 * reported on every emitted bar.
 */

export class TickRunBarsValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TickRunBarsValidationError";
  }
}

export interface Trade {
  tradeId: string;
  timestamp: string;
  session: string;
  symbol: string;
  price: number;
  volume: number;
  currency: string;
  sequence?: number;
}

export interface Config {
  closePartial: boolean;
  initialTickSign: -1 | 1;
  initialExpectedTicks: number;
  initialBuyProbability: number;
  alphaTicks: number;
  alphaBuyProbability: number;
  thresholdFloorTicks: number;
  thresholdMultiplier: number;
}

export type CloseReason = "threshold" | "session_end" | "stream_end";
export type DominantSide = "buy" | "sell" | "tie";

export interface Bar {
  barIndex: number;
  session: string;
  startTime: string;
  endTime: string;
  lastTradeTime: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  dollarValue: number;
  tickCount: number;
  firstTradeId: string;
  lastTradeId: string;
  closeReason: CloseReason;
  buyTicks: number;
  sellTicks: number;
  dominantCount: number;
  dominantSide: DominantSide;
  thresholdTicks: number;
  overshootTicks: number;
  thresholdMet: boolean;
  isComplete: boolean;
  frozenExpectedTicks: number;
  frozenBuyProbability: number;
}

export const REQUIRED_TRADE_FIELDS = [
  "tradeId", "timestamp", "session", "symbol", "price", "volume", "currency",
] as const;

export const REQUIRED_CONFIG_KEYS = [
  "closePartial", "initialTickSign", "initialExpectedTicks", "initialBuyProbability",
  "alphaTicks", "alphaBuyProbability", "thresholdFloorTicks", "thresholdMultiplier",
] as const;

export function timestampMs(value: unknown): number {
  if (typeof value !== "string" || !value.endsWith("Z")) {
    throw new TickRunBarsValidationError("timestamp must be an ISO-8601 UTC string ending in Z");
  }
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) throw new TickRunBarsValidationError("timestamp must be valid ISO-8601");
  return parsed;
}

export function finite(value: unknown, name: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new TickRunBarsValidationError(`${name} must be a finite number`);
  }
  return value;
}

export function positive(value: unknown, name: string): number {
  const parsed = finite(value, name);
  if (parsed <= 0) throw new TickRunBarsValidationError(`${name} must be positive`);
  return parsed;
}

export function rounded(value: number): number {
  return Number(value.toFixed(8));
}

/** Every key is required — this algorithm has no silent defaults. */
export function validateConfig(config: Config): void {
  if (!config || typeof config !== "object") {
    throw new TickRunBarsValidationError("config must be an object");
  }
  const missing = REQUIRED_CONFIG_KEYS.filter((key) => !(key in config));
  if (missing.length) {
    throw new TickRunBarsValidationError(`config is missing: ${missing.join(", ")}`);
  }
  if (typeof config.closePartial !== "boolean") {
    throw new TickRunBarsValidationError("closePartial must be boolean");
  }
  if (config.initialTickSign !== -1 && config.initialTickSign !== 1) {
    throw new TickRunBarsValidationError("initialTickSign must be -1 or +1");
  }
  positive(config.initialExpectedTicks, "initialExpectedTicks");
  const probability = finite(config.initialBuyProbability, "initialBuyProbability");
  if (probability < 0 || probability > 1) {
    throw new TickRunBarsValidationError("initialBuyProbability must be in [0, 1]");
  }
  for (const [name, value] of [
    ["alphaTicks", config.alphaTicks],
    ["alphaBuyProbability", config.alphaBuyProbability],
  ] as const) {
    const alpha = finite(value, name);
    if (alpha <= 0 || alpha > 1) throw new TickRunBarsValidationError(`${name} must be in (0, 1]`);
  }
  positive(config.thresholdFloorTicks, "thresholdFloorTicks");
  positive(config.thresholdMultiplier, "thresholdMultiplier");
}

export function validateTrades(trades: Trade[]): void {
  if (!Array.isArray(trades)) throw new TickRunBarsValidationError("trades must be an array");
  const ids = new Set<string>();
  const closedSessions = new Set<string>();
  let activeSession: string | null = null;
  let priorTime: number | null = null;
  let priorSequence: number | null = null;
  let symbol: string | null = null;
  let currency: string | null = null;

  for (const trade of trades) {
    if (!trade || typeof trade !== "object") {
      throw new TickRunBarsValidationError("each trade must be an object");
    }
    const missing = REQUIRED_TRADE_FIELDS.filter((field) => !(field in trade));
    if (missing.length) {
      throw new TickRunBarsValidationError(`trade is missing: ${missing.join(", ")}`);
    }
    for (const field of ["tradeId", "session", "symbol", "currency"] as const) {
      if (typeof trade[field] !== "string" || !trade[field].trim()) {
        throw new TickRunBarsValidationError(`${field} must be a non-empty string`);
      }
    }
    if (ids.has(trade.tradeId)) throw new TickRunBarsValidationError("tradeId must be unique");
    ids.add(trade.tradeId);

    const currentTime = timestampMs(trade.timestamp);
    if (priorTime !== null && currentTime < priorTime) {
      throw new TickRunBarsValidationError("trades must be chronological");
    }
    if (priorTime !== null && currentTime === priorTime) {
      // Ties must be broken explicitly: the tick rule depends on order.
      const sequence = trade.sequence;
      if (
        !Number.isInteger(sequence) ||
        priorSequence === null ||
        (sequence as number) <= priorSequence
      ) {
        throw new TickRunBarsValidationError(
          "equal timestamps require strictly increasing integer sequence values",
        );
      }
    }
    priorSequence = Number.isInteger(trade.sequence) ? (trade.sequence as number) : null;
    priorTime = currentTime;

    const price = finite(trade.price, "price");
    const volume = finite(trade.volume, "volume");
    if (price <= 0 || volume <= 0) {
      throw new TickRunBarsValidationError("price and volume must be positive");
    }

    if (symbol === null) {
      symbol = trade.symbol;
      currency = trade.currency;
    } else if (trade.symbol !== symbol || trade.currency !== currency) {
      throw new TickRunBarsValidationError("one symbol and one currency are allowed per call");
    }

    if (activeSession === null) activeSession = trade.session;
    else if (trade.session !== activeSession) {
      closedSessions.add(activeSession);
      if (closedSessions.has(trade.session)) {
        throw new TickRunBarsValidationError("a session may not reappear after another session begins");
      }
      activeSession = trade.session;
    }
  }
}

export function constructBars(trades: Trade[], config: Config): Bar[] {
  validateConfig(config);
  validateTrades(trades);
  if (!trades.length) return [];

  const initialSign = config.initialTickSign;
  const initialExpectedTicks = config.initialExpectedTicks;
  const initialBuyProbability = config.initialBuyProbability;
  const alphaTicks = config.alphaTicks;
  const alphaProbability = config.alphaBuyProbability;
  const floor = config.thresholdFloorTicks;
  const multiplier = config.thresholdMultiplier;

  const result: Bar[] = [];
  let current: Trade[] = [];
  let activeSession: string | null = null;
  let previousPrice: number | null = null;
  let tickSign: number = initialSign;
  let expectedTicks = initialExpectedTicks;
  let expectedBuyProbability = initialBuyProbability;
  let buyTicks = 0;
  let sellTicks = 0;
  let threshold = 0;
  let frozenExpectedTicks = 0;
  let frozenBuyProbability = 0;

  const beginBar = (): void => {
    // Snapshot the expectations at bar open so the bar carries its own audit trail.
    current = [];
    buyTicks = 0;
    sellTicks = 0;
    frozenExpectedTicks = expectedTicks;
    frozenBuyProbability = expectedBuyProbability;
    threshold = Math.max(
      floor,
      multiplier * frozenExpectedTicks * Math.max(frozenBuyProbability, 1 - frozenBuyProbability),
    );
  };

  const emit = (reason: CloseReason): void => {
    if (!current.length) return;
    const prices = current.map((t) => t.price);
    const volumes = current.map((t) => t.volume);
    const dominantCount = Math.max(buyTicks, sellTicks);
    const dominantSide: DominantSide =
      buyTicks > sellTicks ? "buy" : sellTicks > buyTicks ? "sell" : "tie";
    const isComplete = reason === "threshold";
    result.push({
      barIndex: result.length,
      session: current[0].session,
      startTime: current[0].timestamp,
      endTime: current.at(-1)!.timestamp,
      lastTradeTime: current.at(-1)!.timestamp,
      open: rounded(prices[0]),
      high: rounded(Math.max(...prices)),
      low: rounded(Math.min(...prices)),
      close: rounded(prices.at(-1)!),
      volume: rounded(volumes.reduce((a, b) => a + b, 0)),
      dollarValue: rounded(current.reduce((a, t) => a + t.price * t.volume, 0)),
      tickCount: current.length,
      firstTradeId: current[0].tradeId,
      lastTradeId: current.at(-1)!.tradeId,
      closeReason: reason,
      buyTicks,
      sellTicks,
      dominantCount,
      dominantSide,
      thresholdTicks: rounded(threshold),
      overshootTicks: isComplete ? rounded(Math.max(0, dominantCount - threshold)) : 0,
      thresholdMet: isComplete,
      isComplete,
      frozenExpectedTicks: rounded(frozenExpectedTicks),
      frozenBuyProbability: rounded(frozenBuyProbability),
    });
    // Only a complete bar is evidence about the process.
    if (isComplete) {
      const observedTicks = current.length;
      const observedBuyProbability = buyTicks / observedTicks;
      expectedTicks = (1 - alphaTicks) * expectedTicks + alphaTicks * observedTicks;
      expectedBuyProbability =
        (1 - alphaProbability) * expectedBuyProbability + alphaProbability * observedBuyProbability;
    }
    beginBar();
  };

  beginBar();
  for (const trade of trades) {
    if (activeSession !== null && trade.session !== activeSession) {
      if (current.length && config.closePartial) emit("session_end");
      else beginBar();
      previousPrice = null;
      tickSign = initialSign;
      expectedTicks = initialExpectedTicks;
      expectedBuyProbability = initialBuyProbability;
      beginBar();
    }
    activeSession = trade.session;

    if (previousPrice !== null) {
      // A flat trade deliberately carries the preceding sign.
      tickSign = trade.price > previousPrice ? 1 : trade.price < previousPrice ? -1 : tickSign;
    }
    previousPrice = trade.price;
    current.push(trade);
    if (tickSign === 1) buyTicks += 1;
    if (tickSign === -1) sellTicks += 1;

    if (Math.max(buyTicks, sellTicks) >= threshold) emit("threshold");
  }

  if (current.length && config.closePartial) emit("stream_end");
  return result;
}
