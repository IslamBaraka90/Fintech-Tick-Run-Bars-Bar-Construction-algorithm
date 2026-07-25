"""StreamingTickRunBarBuilder: batch parity plus observable state."""

import json
from pathlib import Path

import pytest

from fintech_tick_run_bars import (
    StreamingTickRunBarBuilder,
    TickRunBarsValidationError,
    construct_bars,
    load_trades,
)

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = json.loads((FIXTURES / "worked_example.json").read_text())
CONFIG = FIXTURE["config"]
TRADES = FIXTURE["trades"]


def _stream(trades, config):
    builder = StreamingTickRunBarBuilder(config)
    emitted = builder.push_many(trades)
    emitted.extend(builder.flush())
    return emitted


def test_streaming_matches_batch():
    assert _stream(TRADES, CONFIG) == construct_bars(TRADES, CONFIG)


def test_streaming_matches_batch_close_partial_false():
    config = {**CONFIG, "closePartial": False}
    assert _stream(TRADES[:2], config) == construct_bars(TRADES[:2], config)


def test_streaming_matches_batch_over_the_tape():
    tape = load_trades(FIXTURES / "trade_tape.csv")
    assert _stream(tape, CONFIG) == construct_bars(tape, CONFIG)


def test_state_is_observable_and_seeded():
    builder = StreamingTickRunBarBuilder(CONFIG)
    assert builder.expected_ticks == 4
    assert builder.expected_buy_probability == 0.625
    assert builder.threshold == 2.5
    assert builder.buy_ticks == builder.sell_ticks == builder.dominant_count == 0


def test_counts_track_the_documented_signs():
    builder = StreamingTickRunBarBuilder(CONFIG)
    # Bar 0 closes on the 4th trade, so check the first three cumulatively.
    for index, expected in enumerate([(1, 0), (2, 0), (2, 1)]):
        assert builder.push(TRADES[index]) == []
        assert (builder.buy_ticks, builder.sell_ticks) == expected
    closed = builder.push(TRADES[3])
    assert len(closed) == 1 and closed[0]["dominantCount"] == 3


def test_state_updates_after_a_threshold_close():
    builder = StreamingTickRunBarBuilder(CONFIG)
    builder.push_many(TRADES[:4])
    # observed: 4 ticks, buy probability 3/4; alphas 0.5.
    assert builder.expected_ticks == 4.0  # 0.5*4 + 0.5*4
    assert builder.expected_buy_probability == 0.6875  # 0.5*0.625 + 0.5*0.75
    assert builder.threshold == 2.75


def test_dominant_count_is_the_max_not_the_net():
    builder = StreamingTickRunBarBuilder(CONFIG)
    builder.push_many(TRADES[:3])  # + + -
    assert (builder.buy_ticks, builder.sell_ticks) == (2, 1)
    assert builder.dominant_count == 2  # max, not the net imbalance of 1


def test_tick_sign_carries_across_the_bar_boundary():
    builder = StreamingTickRunBarBuilder(CONFIG)
    builder.push_many(TRADES[:4])  # bar 0 closes here, last sign was +1
    assert builder.tick_sign == 1
    builder.push(TRADES[4])  # flat -> carries +1 into the new bar
    assert builder.buy_ticks == 1


def test_flush_closes_the_partial_tail():
    builder = StreamingTickRunBarBuilder(CONFIG)
    builder.push_many(TRADES[:2])
    final = builder.flush()
    assert len(final) == 1 and final[0]["closeReason"] == "stream_end"


def test_cannot_push_after_flush():
    builder = StreamingTickRunBarBuilder(CONFIG)
    builder.push_many(TRADES)
    builder.flush()
    with pytest.raises(TickRunBarsValidationError):
        builder.push(TRADES[0])


def test_streaming_validates_incrementally():
    builder = StreamingTickRunBarBuilder(CONFIG)
    builder.push(TRADES[0])
    with pytest.raises(TickRunBarsValidationError):
        builder.push({**TRADES[1], "tradeId": "W01"})
