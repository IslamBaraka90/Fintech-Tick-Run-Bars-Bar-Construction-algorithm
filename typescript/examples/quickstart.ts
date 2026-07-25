/**
 * Quickstart: Tick-Run Bars, batch and streaming (with adaptive state).
 *
 * Run:  node --experimental-strip-types examples/quickstart.ts
 */

import { constructBars, type Config, type Trade } from "../src/bars.ts";
import { StreamingTickRunBarBuilder } from "../src/streaming.ts";

const config: Config = {
  closePartial: true,
  initialTickSign: 1,
  initialExpectedTicks: 4,
  initialBuyProbability: 0.625,
  alphaTicks: 0.5,
  alphaBuyProbability: 0.5,
  thresholdFloorTicks: 2,
  thresholdMultiplier: 1,
};
const prices = [100.0, 100.0, 99.9, 100.0, 100.0, 99.9, 99.9, 99.8];
const volumes = [10, 20, 15, 25, 10, 12, 18, 14];
const trades: Trade[] = prices.map((price, i) => ({
  tradeId: `W${String(i + 1).padStart(2, "0")}`,
  timestamp: `2026-01-05T14:30:${String(i).padStart(2, "0")}.000Z`,
  session: "S1",
  symbol: "SYNTH",
  price,
  volume: volumes[i],
  currency: "USD",
}));

// 1) Batch: a bar closes when the DOMINANT side's cumulative count hits the threshold.
for (const bar of constructBars(trades, config)) {
  console.log(`bar ${bar.barIndex}: buy=${bar.buyTicks} sell=${bar.sellTicks} dominant=${bar.dominantCount} (${bar.dominantSide}) threshold=${bar.thresholdTicks} overshoot=${bar.overshootTicks} (${bar.closeReason})`);
}

// 2) Streaming: watch the runs build and the threshold adapt.
console.log("--- streaming (adaptive state) ---");
const builder = new StreamingTickRunBarBuilder(config);
console.log(`seed: E[ticks]=${builder.expectedTicks} E[p_buy]=${builder.expectedBuyProbability} threshold=${builder.threshold}`);
for (const trade of trades) {
  const closed = builder.push(trade);
  for (const bar of closed) {
    console.log(`  closed on ${trade.tradeId}: ${bar.dominantCount} ${bar.dominantSide} ticks >= ${bar.thresholdTicks} -> new E[p_buy]=${builder.expectedBuyProbability.toFixed(4)} threshold=${builder.threshold.toFixed(4)}`);
  }
  if (!closed.length) {
    console.log(`  ${trade.tradeId}: buy=${builder.buyTicks} sell=${builder.sellTicks} dominant=${builder.dominantCount} (threshold ${builder.threshold})`);
  }
}
for (const bar of builder.flush()) {
  console.log(`  flushed partial: ticks=${bar.tickCount} (${bar.closeReason})`);
}
