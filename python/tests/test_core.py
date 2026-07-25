"""Exactness and contract tests for Tick-Run Bars.

The worked-example fixture ships the per-trade ``signs`` array and the two
``expectedBars``. Both language suites assert the same numbers.
"""

import json
from pathlib import Path

import pytest

from fintech_tick_run_bars import TickRunBarsValidationError, construct_bars

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "worked_example.json").read_text())
CONFIG = FIXTURE["config"]
TRADES = FIXTURE["trades"]
SIGNS = FIXTURE["signs"]
EXPECTED = FIXTURE["expectedBars"]


def test_worked_example_matches_expected_bars():
    bars = construct_bars(TRADES, CONFIG)
    assert len(bars) == len(EXPECTED) == 2
    for bar, want in zip(bars, EXPECTED):
        for key, value in want.items():
            assert bar[key] == value, key


def test_first_bar_closes_on_dominant_buy_run():
    bar = construct_bars(TRADES, CONFIG)[0]
    # Opening threshold: max(2, 1 * 4 * max(0.625, 0.375)) = 2.5
    assert bar["thresholdTicks"] == 2.5
    assert (bar["buyTicks"], bar["sellTicks"]) == (3, 1)
    assert bar["dominantSide"] == "buy" and bar["dominantCount"] == 3
    assert bar["overshootTicks"] == 0.5  # 3 - 2.5
    assert bar["lastTradeId"] == "W04"


def test_run_means_cumulative_count_not_consecutive_streak():
    # Bar 0's signs are + + - + : the longest consecutive streak is 2, but the
    # dominant cumulative count is 3, which is what closes the bar.
    bar = construct_bars(TRADES, CONFIG)[0]
    bar_signs = SIGNS[:4]
    longest_streak = 1
    run = 1
    for previous, current in zip(bar_signs, bar_signs[1:]):
        run = run + 1 if current == previous else 1
        longest_streak = max(longest_streak, run)
    assert longest_streak == 2
    assert bar["dominantCount"] == 3  # cumulative, not streak
    assert bar["dominantCount"] == bar_signs.count(1)


def test_second_bar_closes_on_dominant_sell_run():
    bar = construct_bars(TRADES, CONFIG)[1]
    # Post-close EWMAs: E[ticks] = 4, E[p] = 0.6875 -> threshold 4 * 0.6875 = 2.75
    assert bar["thresholdTicks"] == 2.75
    assert bar["frozenExpectedTicks"] == 4.0
    assert bar["frozenBuyProbability"] == 0.6875
    assert bar["dominantSide"] == "sell" and bar["dominantCount"] == 3
    assert bar["overshootTicks"] == 0.25


def test_threshold_is_symmetric_in_buy_probability():
    # max(p, 1-p) means a sell-skewed tape raises the threshold just as much.
    buy_skew = construct_bars(TRADES, {**CONFIG, "initialBuyProbability": 0.8})[0]
    sell_skew = construct_bars(TRADES, {**CONFIG, "initialBuyProbability": 0.2})[0]
    assert buy_skew["thresholdTicks"] == sell_skew["thresholdTicks"] == 3.2  # 4 * 0.8


def test_flat_trade_carries_sign_across_a_bar_boundary():
    # W05 is flat against W04 (both 100.00) and opens bar 1 carrying +1.
    assert TRADES[3]["price"] == TRADES[4]["price"]
    assert SIGNS[4] == 1
    assert construct_bars(TRADES, CONFIG)[1]["buyTicks"] == 1


def test_partial_bar_is_not_complete_and_does_not_learn():
    bars = construct_bars(TRADES[:2], CONFIG)  # never reaches 2.5
    assert len(bars) == 1
    assert bars[0]["closeReason"] == "stream_end"
    assert bars[0]["isComplete"] is False and bars[0]["thresholdMet"] is False
    assert bars[0]["overshootTicks"] == 0.0  # not reported for incomplete bars
    assert bars[0]["frozenExpectedTicks"] == 4.0  # seeds untouched


def test_close_partial_false_drops_the_tail():
    assert construct_bars(TRADES[:2], {**CONFIG, "closePartial": False}) == []


def test_threshold_floor_applies():
    bars = construct_bars(TRADES, {**CONFIG, "thresholdFloorTicks": 100})
    assert len(bars) == 1 and bars[0]["closeReason"] == "stream_end"
    assert bars[0]["thresholdTicks"] == 100


def test_tie_is_reported_when_counts_are_equal():
    # Two trades, one up one down, with a floor high enough to avoid closing.
    trades = [
        {**TRADES[0], "tradeId": "T1", "price": 100.0},
        {**TRADES[1], "tradeId": "T2", "price": 99.0},
    ]
    bar = construct_bars(trades, {**CONFIG, "thresholdFloorTicks": 50})[0]
    assert bar["buyTicks"] == bar["sellTicks"] == 1
    assert bar["dominantSide"] == "tie"


def test_empty_trades_returns_empty():
    assert construct_bars([], CONFIG) == []


@pytest.mark.parametrize("missing", sorted(CONFIG))
def test_every_config_key_is_required(missing):
    partial = {k: v for k, v in CONFIG.items() if k != missing}
    with pytest.raises(TickRunBarsValidationError):
        construct_bars(TRADES, partial)


@pytest.mark.parametrize(
    "override",
    [
        {"initialTickSign": 0},
        {"initialExpectedTicks": 0},
        {"initialBuyProbability": 1.5},
        {"initialBuyProbability": -0.1},
        {"alphaTicks": 0},
        {"alphaBuyProbability": 1.5},
        {"thresholdFloorTicks": 0},
        {"thresholdMultiplier": -1},
        {"closePartial": "yes"},
    ],
)
def test_rejects_bad_config(override):
    with pytest.raises(TickRunBarsValidationError):
        construct_bars(TRADES, {**CONFIG, **override})


def test_equal_timestamps_require_a_sequence():
    tied = [TRADES[0], {**TRADES[1], "timestamp": TRADES[0]["timestamp"]}]
    with pytest.raises(TickRunBarsValidationError):
        construct_bars(tied, CONFIG)
    # With strictly increasing sequences it is accepted.
    ok = [
        {**TRADES[0], "sequence": 1},
        {**TRADES[1], "timestamp": TRADES[0]["timestamp"], "sequence": 2},
    ]
    assert construct_bars(ok, CONFIG) != []


def test_rejects_duplicate_trade_id():
    with pytest.raises(TickRunBarsValidationError):
        construct_bars([TRADES[0], {**TRADES[1], "tradeId": "W01"}], CONFIG)


def test_rejects_unordered_trades():
    with pytest.raises(TickRunBarsValidationError):
        construct_bars(list(reversed(TRADES)), CONFIG)


def test_rejects_mixed_symbol():
    with pytest.raises(TickRunBarsValidationError):
        construct_bars([TRADES[0], {**TRADES[1], "symbol": "OTHER"}], CONFIG)


def test_rejects_non_positive_price():
    with pytest.raises(TickRunBarsValidationError):
        construct_bars([{**TRADES[0], "price": 0}], CONFIG)
