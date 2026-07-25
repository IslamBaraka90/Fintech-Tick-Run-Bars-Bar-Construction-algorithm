"""Causal Tick-Run Bars under an explicit, disclosed EWMA convention.

Faithful to the reference algorithm published at The Fintech Builder (topic
``D01-F01-A07``).

**What "run" means here.** A run is the **larger cumulative count** of positive
and negative tick-rule signs in the open bar — *not* the longest consecutive
same-sign streak. The bar closes when the dominant side's running count reaches
the threshold:

    buyTicks  = count of +1 signs in the bar
    sellTicks = count of -1 signs in the bar
    close the bar when  max(buyTicks, sellTicks) >= threshold

That distinction matters: ``+ + - +`` has a longest consecutive streak of 2 but a
dominant cumulative count of 3. This package uses the cumulative reading, which is
the one that tracks sustained one-sided pressure through minor interruptions.

The tick rule is the family standard: uptick ``+1``, downtick ``-1``, flat carries
the preceding sign (**including across a bar boundary**); a session's first trade
uses ``initialTickSign``.

The threshold adapts through two EWMAs, updated only after a **complete**
(threshold-closed) bar, and is driven by the *dominant-side probability*:

    E[ticks] <- (1 - alphaTicks)          * E[ticks] + alphaTicks          * observedTicks
    E[p_buy] <- (1 - alphaBuyProbability) * E[p_buy] + alphaBuyProbability * (buyTicks / observedTicks)

    threshold = max(thresholdFloorTicks,
                    thresholdMultiplier * E[ticks] * max(E[p_buy], 1 - E[p_buy]))

Using ``max(p, 1-p)`` makes the threshold symmetric: a strongly *sell*-skewed
tape raises the bar just as much as a buy-skewed one.

The threshold and both expectations are **frozen when a bar opens**, and every
emitted bar reports that snapshot so it can be audited after the fact.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from numbers import Real
from typing import Any

__all__ = ["TickRunBarsValidationError", "REQUIRED_TRADE_FIELDS", "REQUIRED_CONFIG_KEYS", "construct_bars"]

REQUIRED_TRADE_FIELDS = ("tradeId", "timestamp", "session", "symbol", "price", "volume", "currency")
REQUIRED_CONFIG_KEYS = (
    "closePartial",
    "initialTickSign",
    "initialExpectedTicks",
    "initialBuyProbability",
    "alphaTicks",
    "alphaBuyProbability",
    "thresholdFloorTicks",
    "thresholdMultiplier",
)


class TickRunBarsValidationError(ValueError):
    """Raised when trades or config violate the Tick-Run Bars contract."""


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise TickRunBarsValidationError("timestamp must be an ISO-8601 UTC string ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise TickRunBarsValidationError("timestamp must be valid ISO-8601") from exc
    return parsed.astimezone(timezone.utc)


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TickRunBarsValidationError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise TickRunBarsValidationError(f"{name} must be finite")
    return number


def _positive_config(config: dict[str, Any], key: str) -> float:
    value = _finite_number(config.get(key), key)
    if value <= 0:
        raise TickRunBarsValidationError(f"{key} must be positive")
    return value


def _validate_config(config: dict[str, Any]) -> None:
    """Every key is required — this algorithm has no silent defaults."""
    if not isinstance(config, dict):
        raise TickRunBarsValidationError("config must be a dictionary")
    missing = [key for key in REQUIRED_CONFIG_KEYS if key not in config]
    if missing:
        raise TickRunBarsValidationError(f"config is missing: {', '.join(missing)}")
    if not isinstance(config["closePartial"], bool):
        raise TickRunBarsValidationError("closePartial must be boolean")
    sign = config["initialTickSign"]
    if isinstance(sign, bool) or sign not in (-1, 1):
        raise TickRunBarsValidationError("initialTickSign must be -1 or +1")
    _positive_config(config, "initialExpectedTicks")
    probability = _finite_number(config["initialBuyProbability"], "initialBuyProbability")
    if not 0 <= probability <= 1:
        raise TickRunBarsValidationError("initialBuyProbability must be in [0, 1]")
    for key in ("alphaTicks", "alphaBuyProbability"):
        alpha = _finite_number(config[key], key)
        if not 0 < alpha <= 1:
            raise TickRunBarsValidationError(f"{key} must be in (0, 1]")
    _positive_config(config, "thresholdFloorTicks")
    _positive_config(config, "thresholdMultiplier")


def _validate_trades(trades: list[dict[str, Any]]) -> None:
    if not isinstance(trades, list):
        raise TickRunBarsValidationError("trades must be a list")
    ids: set[str] = set()
    closed_sessions: set[str] = set()
    active_session: str | None = None
    prior_time: datetime | None = None
    prior_sequence: int | None = None
    symbol: str | None = None
    currency: str | None = None

    for trade in trades:
        if not isinstance(trade, dict):
            raise TickRunBarsValidationError("each trade must be a dictionary")
        missing = [field for field in REQUIRED_TRADE_FIELDS if field not in trade]
        if missing:
            raise TickRunBarsValidationError(f"trade is missing: {', '.join(missing)}")
        for field in ("tradeId", "session", "symbol", "currency"):
            if not isinstance(trade[field], str) or not trade[field].strip():
                raise TickRunBarsValidationError(f"{field} must be a non-empty string")
        if trade["tradeId"] in ids:
            raise TickRunBarsValidationError("tradeId must be unique")
        ids.add(trade["tradeId"])

        current_time = _timestamp(trade["timestamp"])
        if prior_time is not None and current_time < prior_time:
            raise TickRunBarsValidationError("trades must be chronological")
        if prior_time is not None and current_time == prior_time:
            # Ties must be broken explicitly: the tick rule depends on order.
            sequence = trade.get("sequence")
            if (
                isinstance(sequence, bool)
                or not isinstance(sequence, int)
                or prior_sequence is None
                or sequence <= prior_sequence
            ):
                raise TickRunBarsValidationError(
                    "equal timestamps require strictly increasing integer sequence values"
                )
        prior_sequence = trade.get("sequence") if isinstance(trade.get("sequence"), int) else None
        prior_time = current_time

        price = _finite_number(trade["price"], "price")
        volume = _finite_number(trade["volume"], "volume")
        if price <= 0 or volume <= 0:
            raise TickRunBarsValidationError("price and volume must be positive")

        if symbol is None:
            symbol, currency = trade["symbol"], trade["currency"]
        elif trade["symbol"] != symbol or trade["currency"] != currency:
            raise TickRunBarsValidationError("one symbol and one currency are allowed per call")

        if active_session is None:
            active_session = trade["session"]
        elif trade["session"] != active_session:
            closed_sessions.add(active_session)
            if trade["session"] in closed_sessions:
                raise TickRunBarsValidationError(
                    "a session may not reappear after another session begins"
                )
            active_session = trade["session"]


def _rounded(value: float) -> float:
    return round(value + 0.0, 8)


def construct_bars(trades: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return deterministic session-local Tick-Run Bars.

    The first trade of each session uses ``initialTickSign``. Thereafter an uptick
    is +1, a downtick is -1, and a flat trade carries the prior sign, **including
    across a bar boundary**. Price, sign, expectations, and open-bar state reset
    only at a session boundary. Only threshold-closed bars update the EWMAs.
    """
    _validate_config(config)
    _validate_trades(trades)
    if not trades:
        return []

    initial_sign = int(config["initialTickSign"])
    initial_expected_ticks = float(config["initialExpectedTicks"])
    initial_buy_probability = float(config["initialBuyProbability"])
    alpha_ticks = float(config["alphaTicks"])
    alpha_probability = float(config["alphaBuyProbability"])
    threshold_floor = float(config["thresholdFloorTicks"])
    multiplier = float(config["thresholdMultiplier"])

    result: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    active_session: str | None = None
    previous_price: float | None = None
    tick_sign = initial_sign
    expected_ticks = initial_expected_ticks
    expected_buy_probability = initial_buy_probability
    buy_ticks = 0
    sell_ticks = 0
    threshold = 0.0
    frozen_expected_ticks = 0.0
    frozen_buy_probability = 0.0

    def reset_session_state() -> None:
        nonlocal previous_price, tick_sign, expected_ticks, expected_buy_probability
        previous_price = None
        tick_sign = initial_sign
        expected_ticks = initial_expected_ticks
        expected_buy_probability = initial_buy_probability

    def begin_bar() -> None:
        # Snapshot the expectations at bar open so the bar carries its own audit trail.
        nonlocal current, buy_ticks, sell_ticks, threshold
        nonlocal frozen_expected_ticks, frozen_buy_probability
        current = []
        buy_ticks = 0
        sell_ticks = 0
        frozen_expected_ticks = expected_ticks
        frozen_buy_probability = expected_buy_probability
        threshold = max(
            threshold_floor,
            multiplier
            * frozen_expected_ticks
            * max(frozen_buy_probability, 1 - frozen_buy_probability),
        )

    def emit(reason: str) -> None:
        nonlocal expected_ticks, expected_buy_probability
        if not current:
            return
        prices = [float(trade["price"]) for trade in current]
        volumes = [float(trade["volume"]) for trade in current]
        dominant_count = max(buy_ticks, sell_ticks)
        dominant_side = (
            "buy" if buy_ticks > sell_ticks else "sell" if sell_ticks > buy_ticks else "tie"
        )
        is_complete = reason == "threshold"
        result.append(
            {
                "barIndex": len(result),
                "session": current[0]["session"],
                "startTime": current[0]["timestamp"],
                "endTime": current[-1]["timestamp"],
                "lastTradeTime": current[-1]["timestamp"],
                "open": _rounded(prices[0]),
                "high": _rounded(max(prices)),
                "low": _rounded(min(prices)),
                "close": _rounded(prices[-1]),
                "volume": _rounded(sum(volumes)),
                "dollarValue": _rounded(sum(p * v for p, v in zip(prices, volumes))),
                "tickCount": len(current),
                "firstTradeId": current[0]["tradeId"],
                "lastTradeId": current[-1]["tradeId"],
                "closeReason": reason,
                "buyTicks": buy_ticks,
                "sellTicks": sell_ticks,
                "dominantCount": dominant_count,
                "dominantSide": dominant_side,
                "thresholdTicks": _rounded(threshold),
                "overshootTicks": _rounded(max(0.0, dominant_count - threshold)) if is_complete else 0.0,
                "thresholdMet": is_complete,
                "isComplete": is_complete,
                "frozenExpectedTicks": _rounded(frozen_expected_ticks),
                "frozenBuyProbability": _rounded(frozen_buy_probability),
            }
        )
        # Only a complete bar is evidence about the process.
        if is_complete:
            observed_ticks = len(current)
            observed_buy_probability = buy_ticks / observed_ticks
            expected_ticks = (1 - alpha_ticks) * expected_ticks + alpha_ticks * observed_ticks
            expected_buy_probability = (
                (1 - alpha_probability) * expected_buy_probability
                + alpha_probability * observed_buy_probability
            )
        begin_bar()

    begin_bar()
    for trade in trades:
        if active_session is not None and trade["session"] != active_session:
            if current and config["closePartial"]:
                emit("session_end")
            else:
                begin_bar()
            reset_session_state()
            begin_bar()
        active_session = trade["session"]

        price = float(trade["price"])
        if previous_price is not None:
            if price > previous_price:
                tick_sign = 1
            elif price < previous_price:
                tick_sign = -1
            # A flat trade deliberately carries the preceding sign.
        previous_price = price
        current.append(trade)
        buy_ticks += int(tick_sign == 1)
        sell_ticks += int(tick_sign == -1)

        if max(buy_ticks, sell_ticks) >= threshold:
            emit("threshold")

    if current and config["closePartial"]:
        emit("stream_end")
    return result
