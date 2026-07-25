/**
 * Fintech Tick-Run Bars — dominant-side run bars.
 *
 * Companion article (canonical): https://thefintechbuilder.com/market-data-engineering/bar-construction/tick-run-bars/
 * Catalog topic id: D01-F01-A07 (Domain D01 — Market Data Engineering / Family D01-F01 — Bar Construction)
 */

export {
  TickRunBarsValidationError,
  constructBars,
  type Trade,
  type Config,
  type Bar,
  type CloseReason,
  type DominantSide,
} from "./bars.ts";
export { StreamingTickRunBarBuilder } from "./streaming.ts";
export { loadTrades } from "./tape.ts";
