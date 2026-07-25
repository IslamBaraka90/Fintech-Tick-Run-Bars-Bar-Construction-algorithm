"""Trade-tape loader tests (run bars need per-trade prices to sign ticks)."""

import json
from pathlib import Path

from fintech_tick_run_bars import construct_bars, load_trades

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = json.loads((FIXTURES / "worked_example.json").read_text())["config"]


def test_load_trades_parses_tape():
    trades = load_trades(FIXTURES / "trade_tape.csv")
    assert len(trades) == 8
    assert trades[0]["tradeId"] == "W01"
    assert trades[0]["price"] == 100.0


def test_construct_bars_over_loaded_tape():
    bars = construct_bars(load_trades(FIXTURES / "trade_tape.csv"), CONFIG)
    assert [b["dominantSide"] for b in bars] == ["buy", "sell"]
    assert [b["thresholdTicks"] for b in bars] == [2.5, 2.75]
    assert [b["overshootTicks"] for b in bars] == [0.5, 0.25]
