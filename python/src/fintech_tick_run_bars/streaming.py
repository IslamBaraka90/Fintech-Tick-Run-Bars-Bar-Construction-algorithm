"""Stateful, streaming Tick-Run-Bar builder.

:func:`~fintech_tick_run_bars.core.construct_bars` aggregates a whole tape at
once. A live tape needs a builder that accepts one trade at a time and emits a
bar the instant ``max(buyTicks, sellTicks) >= threshold``.

Because this bar type is *adaptive*, the builder also **exposes its state**:
:attr:`buy_ticks`, :attr:`sell_ticks`, :attr:`dominant_count`,
:attr:`threshold` (the frozen snapshot the open bar is judged against),
:attr:`expected_ticks`, :attr:`expected_buy_probability`, and :attr:`tick_sign`.

Feed trades with :meth:`push` (returns any bars closed by that trade — zero or
one); call :meth:`flush` at end of stream. Bars are byte-identical to the batch
kernel.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .core import (
    REQUIRED_TRADE_FIELDS,
    TickRunBarsValidationError,
    _finite_number,
    _rounded,
    _timestamp,
    _validate_config,
)

__all__ = ["StreamingTickRunBarBuilder"]


class StreamingTickRunBarBuilder:
    """Incremental Tick-Run-Bar builder with observable adaptive state.

    Examples
    --------
    >>> builder = StreamingTickRunBarBuilder(config)        # doctest: +SKIP
    >>> closed = builder.push_many(tape) + builder.flush()  # doctest: +SKIP
    >>> builder.dominant_count, builder.threshold           # doctest: +SKIP
    """

    def __init__(self, config: dict[str, Any]) -> None:
        _validate_config(config)
        self._close_partial = bool(config["closePartial"])
        self._initial_sign = int(config["initialTickSign"])
        self._initial_expected_ticks = float(config["initialExpectedTicks"])
        self._initial_buy_probability = float(config["initialBuyProbability"])
        self._alpha_ticks = float(config["alphaTicks"])
        self._alpha_probability = float(config["alphaBuyProbability"])
        self._floor = float(config["thresholdFloorTicks"])
        self._multiplier = float(config["thresholdMultiplier"])

        # validation state
        self._ids: set[str] = set()
        self._closed_sessions: set[str] = set()
        self._validation_session: str | None = None
        self._prior_time: datetime | None = None
        self._prior_sequence: int | None = None
        self._symbol: str | None = None
        self._currency: str | None = None

        # adaptive + aggregation state
        self._current: list[dict[str, Any]] = []
        self._session: str | None = None
        self._previous_price: float | None = None
        self._tick_sign = self._initial_sign
        self._expected_ticks = self._initial_expected_ticks
        self._expected_buy_probability = self._initial_buy_probability
        self._buy_ticks = 0
        self._sell_ticks = 0
        self._threshold = 0.0
        self._frozen_expected_ticks = 0.0
        self._frozen_buy_probability = 0.0
        self._bar_count = 0
        self._flushed = False
        self._begin_bar()

    # -- observable state --------------------------------------------------- #
    @property
    def buy_ticks(self) -> int:
        """Cumulative ``+1`` signs in the open bar."""
        return self._buy_ticks

    @property
    def sell_ticks(self) -> int:
        """Cumulative ``-1`` signs in the open bar."""
        return self._sell_ticks

    @property
    def dominant_count(self) -> int:
        """The run length being tested: ``max(buy_ticks, sell_ticks)``."""
        return max(self._buy_ticks, self._sell_ticks)

    @property
    def threshold(self) -> float:
        """Frozen threshold (ticks) the open bar is being judged against."""
        return self._threshold

    @property
    def expected_ticks(self) -> float:
        """Live EWMA estimate of ticks per bar (feeds the *next* bar's threshold)."""
        return self._expected_ticks

    @property
    def expected_buy_probability(self) -> float:
        """Live EWMA estimate of the buy-tick probability."""
        return self._expected_buy_probability

    @property
    def tick_sign(self) -> int:
        """Sign the next flat trade would carry (+1 or -1)."""
        return self._tick_sign

    @property
    def tick_count(self) -> int:
        """Trades currently held in the open bar."""
        return len(self._current)

    # -- internals ---------------------------------------------------------- #
    def _begin_bar(self) -> None:
        self._current = []
        self._buy_ticks = 0
        self._sell_ticks = 0
        self._frozen_expected_ticks = self._expected_ticks
        self._frozen_buy_probability = self._expected_buy_probability
        self._threshold = max(
            self._floor,
            self._multiplier
            * self._frozen_expected_ticks
            * max(self._frozen_buy_probability, 1 - self._frozen_buy_probability),
        )

    def _validate_trade(self, trade: object) -> None:
        if not isinstance(trade, dict):
            raise TickRunBarsValidationError("each trade must be a dictionary")
        missing = [field for field in REQUIRED_TRADE_FIELDS if field not in trade]
        if missing:
            raise TickRunBarsValidationError(f"trade is missing: {', '.join(missing)}")
        for field in ("tradeId", "session", "symbol", "currency"):
            if not isinstance(trade[field], str) or not trade[field].strip():
                raise TickRunBarsValidationError(f"{field} must be a non-empty string")
        if trade["tradeId"] in self._ids:
            raise TickRunBarsValidationError("tradeId must be unique")
        self._ids.add(trade["tradeId"])

        current_time = _timestamp(trade["timestamp"])
        if self._prior_time is not None and current_time < self._prior_time:
            raise TickRunBarsValidationError("trades must be chronological")
        if self._prior_time is not None and current_time == self._prior_time:
            sequence = trade.get("sequence")
            if (
                isinstance(sequence, bool)
                or not isinstance(sequence, int)
                or self._prior_sequence is None
                or sequence <= self._prior_sequence
            ):
                raise TickRunBarsValidationError(
                    "equal timestamps require strictly increasing integer sequence values"
                )
        self._prior_sequence = (
            trade.get("sequence") if isinstance(trade.get("sequence"), int) else None
        )
        self._prior_time = current_time

        price = _finite_number(trade["price"], "price")
        volume = _finite_number(trade["volume"], "volume")
        if price <= 0 or volume <= 0:
            raise TickRunBarsValidationError("price and volume must be positive")

        if self._symbol is None:
            self._symbol, self._currency = trade["symbol"], trade["currency"]
        elif trade["symbol"] != self._symbol or trade["currency"] != self._currency:
            raise TickRunBarsValidationError("one symbol and one currency are allowed per call")

        if self._validation_session is None:
            self._validation_session = trade["session"]
        elif trade["session"] != self._validation_session:
            self._closed_sessions.add(self._validation_session)
            if trade["session"] in self._closed_sessions:
                raise TickRunBarsValidationError(
                    "a session may not reappear after another session begins"
                )
            self._validation_session = trade["session"]

    def _emit(self, reason: str) -> dict[str, Any] | None:
        if not self._current:
            return None
        prices = [float(t["price"]) for t in self._current]
        volumes = [float(t["volume"]) for t in self._current]
        dominant_count = max(self._buy_ticks, self._sell_ticks)
        dominant_side = (
            "buy"
            if self._buy_ticks > self._sell_ticks
            else "sell" if self._sell_ticks > self._buy_ticks else "tie"
        )
        is_complete = reason == "threshold"
        bar = {
            "barIndex": self._bar_count,
            "session": self._current[0]["session"],
            "startTime": self._current[0]["timestamp"],
            "endTime": self._current[-1]["timestamp"],
            "lastTradeTime": self._current[-1]["timestamp"],
            "open": _rounded(prices[0]),
            "high": _rounded(max(prices)),
            "low": _rounded(min(prices)),
            "close": _rounded(prices[-1]),
            "volume": _rounded(sum(volumes)),
            "dollarValue": _rounded(sum(p * v for p, v in zip(prices, volumes))),
            "tickCount": len(self._current),
            "firstTradeId": self._current[0]["tradeId"],
            "lastTradeId": self._current[-1]["tradeId"],
            "closeReason": reason,
            "buyTicks": self._buy_ticks,
            "sellTicks": self._sell_ticks,
            "dominantCount": dominant_count,
            "dominantSide": dominant_side,
            "thresholdTicks": _rounded(self._threshold),
            "overshootTicks": _rounded(max(0.0, dominant_count - self._threshold))
            if is_complete
            else 0.0,
            "thresholdMet": is_complete,
            "isComplete": is_complete,
            "frozenExpectedTicks": _rounded(self._frozen_expected_ticks),
            "frozenBuyProbability": _rounded(self._frozen_buy_probability),
        }
        self._bar_count += 1
        if is_complete:
            observed_ticks = len(self._current)
            observed_buy_probability = self._buy_ticks / observed_ticks
            self._expected_ticks = (
                1 - self._alpha_ticks
            ) * self._expected_ticks + self._alpha_ticks * observed_ticks
            self._expected_buy_probability = (
                1 - self._alpha_probability
            ) * self._expected_buy_probability + self._alpha_probability * observed_buy_probability
        self._begin_bar()
        return bar

    # -- public API --------------------------------------------------------- #
    def push(self, trade: dict[str, Any]) -> list[dict[str, Any]]:
        """Accept one trade and return any bars it closes (zero or one)."""
        if self._flushed:
            raise TickRunBarsValidationError("cannot push after flush()")
        self._validate_trade(trade)
        emitted: list[dict[str, Any]] = []

        if self._session is not None and trade["session"] != self._session:
            if self._current and self._close_partial:
                bar = self._emit("session_end")
                if bar is not None:
                    emitted.append(bar)
            else:
                self._begin_bar()
            # Session reset: price, sign, and both expectations return to seeds.
            self._previous_price = None
            self._tick_sign = self._initial_sign
            self._expected_ticks = self._initial_expected_ticks
            self._expected_buy_probability = self._initial_buy_probability
            self._begin_bar()
        self._session = trade["session"]

        price = float(trade["price"])
        if self._previous_price is not None:
            if price > self._previous_price:
                self._tick_sign = 1
            elif price < self._previous_price:
                self._tick_sign = -1
        self._previous_price = price
        self._current.append(trade)
        self._buy_ticks += int(self._tick_sign == 1)
        self._sell_ticks += int(self._tick_sign == -1)

        if max(self._buy_ticks, self._sell_ticks) >= self._threshold:
            bar = self._emit("threshold")
            if bar is not None:
                emitted.append(bar)
        return emitted

    def push_many(self, trades: object) -> list[dict[str, Any]]:
        """Feed an iterable of trades, returning every bar closed along the way."""
        try:
            iterator = iter(trades)  # type: ignore[arg-type]
        except TypeError as error:
            raise TickRunBarsValidationError("trades must be an iterable.") from error
        emitted: list[dict[str, Any]] = []
        for trade in iterator:
            emitted.extend(self.push(trade))
        return emitted

    def flush(self) -> list[dict[str, Any]]:
        """Close the final partial bar (if ``closePartial``) and end the stream."""
        self._flushed = True
        if self._current and self._close_partial:
            bar = self._emit("stream_end")
            return [bar] if bar is not None else []
        return []
