"""Fintech Tick-Run Bars — dominant-side run bars.

A small, well-specified, cross-language reference implementation of Tick-Run
Bars: a bar closes when the **dominant side's cumulative tick count** reaches a
threshold that adapts, via EWMA, to recent bar length and buy-tick probability.
Every bar reports the frozen threshold it was judged against.

Note: "run" here means the larger cumulative count of + and - signs, not the
longest consecutive same-sign streak.

Companion article (canonical): https://thefintechbuilder.com/market-data-engineering/bar-construction/tick-run-bars/
Catalog topic id: D01-F01-A07  (Domain D01 — Market Data Engineering / Family D01-F01 — Bar Construction)
"""

from __future__ import annotations

from .core import TickRunBarsValidationError, construct_bars
from .streaming import StreamingTickRunBarBuilder
from .tape import load_trades

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "TickRunBarsValidationError",
    "construct_bars",
    "StreamingTickRunBarBuilder",
    "load_trades",
]
