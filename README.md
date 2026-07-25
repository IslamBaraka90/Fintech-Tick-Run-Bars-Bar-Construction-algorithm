# Fintech Tick-Run Bars — Bar Construction Algorithm

> A canonical, well-specified, **cross-language (Python + TypeScript)** reference
> implementation of **Tick-Run Bars** — information-driven bars that close when
> the **dominant side's cumulative tick run** reaches an **EWMA-adaptive
> threshold**, with every bar reporting the frozen threshold it was judged against.

<p>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-blue">
  <img alt="TypeScript" src="https://img.shields.io/badge/typescript-5.7%2B-3178c6">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="Tests" src="https://img.shields.io/badge/tests-46%20py%20%2F%2031%20ts-brightgreen">
</p>

**📖 Full article (canonical):** **[Tick-Run Bars — The Fintech Builder](https://thefintechbuilder.com/market-data-engineering/bar-construction/tick-run-bars/)**

This repository is the runnable, production-oriented companion to that article.
The article teaches the concept; this repo is the code you install and build on.

🧭 **Browse all algorithms:** [Awesome FinTech Algorithms](https://github.com/IslamBaraka90/Fintech-Algorithms-Awesome) — the full index of the library.
🗂️ **This algorithm's domain:** [Market Data Engineering](https://thefintechbuilder.com/domains/market-data-engineering/) › **Bar Construction**
🏁 **Completes the family:** [Time](https://github.com/IslamBaraka90/Fintech-Time-Bars-Bar-Construction-algorithm) · [Tick](https://github.com/IslamBaraka90/Fintech-Tick-Bars-Bar-Construction-algorithm) · [Volume](https://github.com/IslamBaraka90/Fintech-Volume-Bars-Bar-Construction-algorithm) · [Dollar](https://github.com/IslamBaraka90/Fintech-Dollar-Bars-Bar-Construction-algorithm) · [Tick-Imbalance](https://github.com/IslamBaraka90/Fintech-Tick-Imbalance-Bars-Bar-Construction-algorithm) · [Volume-Imbalance](https://github.com/IslamBaraka90/Fintech-Volume-Imbalance-Bars-Bar-Construction-algorithm) · **Tick-Run**.

| | |
|---|---|
| **Catalog topic** | `D01-F01-A07` |
| **Domain** | D01 — Market Data Engineering |
| **Family** | D01-F01 — Bar Construction |
| **Difficulty** | 4 / 5 |
| **Languages** | Python, TypeScript |

---

## Table of contents

- [What "run" actually means here](#what-run-actually-means-here)
- [How the threshold adapts](#how-the-threshold-adapts)
- [Why this implementation](#why-this-implementation)
- [Install](#install)
- [Quickstart](#quickstart)
- [Streaming, with observable state](#streaming-with-observable-state)
- [Loading a trade tape](#loading-a-trade-tape)
- [Config & bar shapes](#config--bar-shapes)
- [Worked example (exact)](#worked-example-exact)
- [API reference](#api-reference)
- [Edge cases & limitations](#edge-cases--limitations)
- [Testing](#testing)
- [Related algorithms](#related-algorithms)
- [License](#license)

---

## What "run" actually means here

This is the single most important thing to get right, and it's where naive
implementations diverge.

**A "run" is the larger cumulative count of `+1` and `-1` tick signs in the open
bar — *not* the longest consecutive same-sign streak.**

```
buyTicks  = count of +1 signs in the bar
sellTicks = count of -1 signs in the bar
close the bar when  max(buyTicks, sellTicks) >= threshold
```

Consider the sign sequence `+ + − +`:

| reading | value |
|---|--:|
| longest **consecutive** streak | 2 |
| dominant **cumulative** count (what this package uses) | **3** |

The cumulative reading is what tracks *sustained one-sided pressure through minor
interruptions* — a single opposing print shouldn't reset your measure of a buying
campaign. The test suite pins this down explicitly: it computes the longest
consecutive streak of bar 0 (which is 2), and asserts the bar actually closed on
a dominant count of 3.

The tick rule itself is the family standard: uptick `+1`, downtick `-1`, and a
flat trade carries the **preceding** sign — **including across a bar boundary**
(also directly tested). A session's first trade uses `initialTickSign`.

## How the threshold adapts

Two EWMAs, updated **only after a complete (threshold-closed) bar**:

```
E[ticks] ← (1 − alphaTicks)          · E[ticks] + alphaTicks          · observedTicks
E[p_buy] ← (1 − alphaBuyProbability) · E[p_buy] + alphaBuyProbability · (buyTicks / observedTicks)

threshold = max(thresholdFloorTicks,
                thresholdMultiplier · E[ticks] · max(E[p_buy], 1 − E[p_buy]))
```

The `max(p, 1 − p)` term is the design choice worth noticing: it makes the
threshold **symmetric**. A tape skewed 80% *sell* raises the bar exactly as much
as one skewed 80% *buy* — the algorithm cares about how one-sided flow is, not
which side it favors. (Tested directly: `p = 0.8` and `p = 0.2` yield the same
threshold.)

Two guards, shared with the imbalance siblings:

- **Partial bars never learn.** `session_end` / `stream_end` bars are artifacts of
  where the tape stopped (`isComplete: false`), so they don't move expectations —
  and `overshootTicks` is reported as `0` for them rather than a misleading number.
- **A floor.** `thresholdFloorTicks` stops the threshold collapsing toward zero.

The threshold and both expectations are **frozen when a bar opens**, and each bar
reports that snapshot (`thresholdTicks`, `frozenExpectedTicks`,
`frozenBuyProbability`) so it can be audited without re-deriving anything.

## Why this implementation

- **The correct run semantics**, stated up front and enforced by tests.
- **Self-auditing bars** — frozen snapshot plus `buyTicks` / `sellTicks` /
  `dominantCount` / `dominantSide` (`buy` / `sell` / **`tie`**) / `overshootTicks`.
- **No silent defaults.** Every config key is required; a missing key raises
  rather than quietly changing your bars. (Each of the 8 keys has its own test.)
- **Order safety.** Equal timestamps *require* a strictly increasing `sequence` —
  the tick rule depends on order, so ties can't be resolved arbitrarily.
- **Observable adaptation** in the streaming builder.
- **Cross-language parity** — both suites assert the fixture's `signs` array and
  both `expectedBars` verbatim.

## Install

**Python**

```bash
pip install fintech-tick-run-bars
```

**TypeScript / JavaScript (Node ≥ 20)**

```bash
npm install fintech-tick-run-bars
```

## Quickstart

**Python**

```python
from fintech_tick_run_bars import construct_bars

config = {
    "closePartial": True, "initialTickSign": 1,
    "initialExpectedTicks": 20, "initialBuyProbability": 0.5,
    "alphaTicks": 0.2, "alphaBuyProbability": 0.2,
    "thresholdFloorTicks": 5, "thresholdMultiplier": 1,
}
bars = construct_bars(trades, config)
```

**TypeScript**

```ts
import { constructBars } from "fintech-tick-run-bars";

const bars = constructBars(trades, config);
```

## Streaming, with observable state

```python
from fintech_tick_run_bars import StreamingTickRunBarBuilder

builder = StreamingTickRunBarBuilder(config)
for trade in tape:
    for bar in builder.push(trade):
        publish(bar)
    monitor(builder.buy_ticks, builder.sell_ticks, builder.dominant_count, builder.threshold)
for bar in builder.flush():
    publish(bar)
```

The bundled example prints the runs building and the threshold adapting:

```
seed: E[ticks]=4 E[p_buy]=0.625 threshold=2.5
  W01: buy=1 sell=0 dominant=1 (threshold 2.5)
  ...
  closed on W04: 3 buy ticks >= 2.5 -> new E[p_buy]=0.6875 threshold=2.7500
```

## Loading a trade tape

Run bars need each execution's **price** to assign a tick sign, so they are built
from a trade tape. Yahoo Finance does not expose tick data — only pre-aggregated
bars, where the per-trade signs are already lost — so there is no live Yahoo
source for this algorithm.

```python
from fintech_tick_run_bars import construct_bars, load_trades

trades = load_trades("tape.csv")   # tradeId,timestamp,session,symbol,price,volume,currency
bars = construct_bars(trades, config)
```

> **Data note:** the committed fixtures are synthetic teaching data (CC0-style)
> and prove the package mechanics only. They are not a market episode or a
> predictive result.

## Config & bar shapes

**Config** — all eight keys are **required**:

| Key | Meaning |
|---|---|
| `closePartial` | emit trailing partial bars |
| `initialTickSign` | sign for a session's first trade (`-1` or `1`) |
| `initialExpectedTicks` | positive seed for `E[ticks]` |
| `initialBuyProbability` | seed for `E[p_buy]`, in `[0, 1]` |
| `alphaTicks`, `alphaBuyProbability` | EWMA weights in `(0, 1]` |
| `thresholdFloorTicks` | positive lower bound (ticks) |
| `thresholdMultiplier` | positive scale on the expectation product |

**Bar:** the usual OHLCV/audit fields plus `buyTicks`, `sellTicks`,
`dominantCount`, `dominantSide`, `thresholdTicks`, `overshootTicks`,
`thresholdMet`, `isComplete`, `frozenExpectedTicks`, `frozenBuyProbability`.

## Worked example (exact)

Seeds `E[ticks] = 4`, `E[p_buy] = 0.625`, floor `2`, multiplier `1`, alphas `0.5`
⇒ opening threshold `max(2, 4 · max(0.625, 0.375)) = 2.5`.

Signs: `+ + − + | + − − −`

| bar | trades | signs | buy | sell | dominant | threshold | overshoot |
|---|---|---|--:|--:|--:|--:|--:|
| 0 | W01–W04 | `+ + − +` | 3 | 1 | **3 buy** | 2.5 | 0.5 |
| 1 | W05–W08 | `+ − − −` | 1 | 3 | **3 sell** | 2.75 | 0.25 |

After bar 0: `E[ticks] = 0.5·4 + 0.5·4 = 4`,
`E[p_buy] = 0.5·0.625 + 0.5·0.75 = 0.6875`, so bar 1's threshold is
`4 · 0.6875 = 2.75`. Note W05 is *flat* against W04 and carries `+1` **across the
bar boundary**. Every number here is asserted by **both** language test suites.

## API reference

| Purpose | Python | TypeScript |
|---|---|---|
| Batch construction | `construct_bars(trades, config)` | `constructBars(trades, config)` |
| Streaming builder | `StreamingTickRunBarBuilder(config)` | `new StreamingTickRunBarBuilder(config)` |
| Observable state | `.buy_ticks`, `.sell_ticks`, `.dominant_count`, `.threshold`, `.expected_ticks`, `.expected_buy_probability`, `.tick_sign` | `.buyTicks`, `.sellTicks`, `.dominantCount`, `.threshold`, `.expectedTicks`, `.expectedBuyProbability`, `.tickSign` |
| Load a trade tape | `load_trades(csv)` | `loadTrades(path)` |
| Errors | `TickRunBarsValidationError` | `TickRunBarsValidationError` |

## Edge cases & limitations

- **Runs are cumulative, not consecutive** — see the section above before
  comparing against another library.
- **Seed sensitivity:** early bars are dominated by your seeds; treat them as burn-in.
- **Floor matters:** without `thresholdFloorTicks`, a balanced tape (`p → 0.5`)
  with small `E[ticks]` drives the threshold low and emits tiny bars.
- **Ties are real:** with equal buy/sell counts `dominantSide` is `"tie"`.
- **Not a predictor:** runs describe realized flow, not future direction.
- **Sessions reset everything** — previous price, tick sign, and both expectations.
- **Equal timestamps require a `sequence`** — the tick rule depends on order.

## Testing

**Python** (46 tests)

```bash
cd python && pip install -e ".[dev]" && pytest
```

**TypeScript** (31 tests, zero runtime dependencies)

```bash
cd typescript && npm install && npm test && npm run build
```

## Related algorithms

- `D01-F01-A05` — [Tick-Imbalance Bars](https://github.com/IslamBaraka90/Fintech-Tick-Imbalance-Bars-Bar-Construction-algorithm) (net imbalance instead of dominant run)
- `D01-F01-A06` — [Volume-Imbalance Bars](https://github.com/IslamBaraka90/Fintech-Volume-Imbalance-Bars-Bar-Construction-algorithm) (size-weighted)
- `D01-F01-A01…A04` — [Time](https://github.com/IslamBaraka90/Fintech-Time-Bars-Bar-Construction-algorithm) · [Tick](https://github.com/IslamBaraka90/Fintech-Tick-Bars-Bar-Construction-algorithm) · [Volume](https://github.com/IslamBaraka90/Fintech-Volume-Bars-Bar-Construction-algorithm) · [Dollar](https://github.com/IslamBaraka90/Fintech-Dollar-Bars-Bar-Construction-algorithm)
- `D07-F01-A02` — [EMA](https://github.com/IslamBaraka90/Fintech-EMA-Exponential-Moving-Average-algorithm) (the smoothing behind the adaptive threshold)

Full index: **[Awesome FinTech Algorithms](https://github.com/IslamBaraka90/Fintech-Algorithms-Awesome)**.

## License

[MIT](./LICENSE) © The Fintech Builder. Part of the
[100 FinTech Algorithms](https://thefintechbuilder.com) library.
