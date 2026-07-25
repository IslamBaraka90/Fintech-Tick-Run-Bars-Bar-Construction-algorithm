import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

import { TickRunBarsValidationError, constructBars, type Config, type Trade } from "../src/bars.ts";

const FIXTURE = JSON.parse(
  readFileSync(fileURLToPath(new URL("./fixtures/worked_example.json", import.meta.url)), "utf8"),
);
const CONFIG: Config = FIXTURE.config;
const TRADES: Trade[] = FIXTURE.trades;
const SIGNS: number[] = FIXTURE.signs;
const EXPECTED: Array<Record<string, unknown>> = FIXTURE.expectedBars;

test("worked example matches expected bars", () => {
  const bars = constructBars(TRADES, CONFIG);
  assert.equal(bars.length, EXPECTED.length);
  assert.equal(bars.length, 2);
  bars.forEach((bar, index) => {
    for (const [key, value] of Object.entries(EXPECTED[index])) {
      assert.equal((bar as unknown as Record<string, unknown>)[key], value, key);
    }
  });
});

test("first bar closes on dominant buy run", () => {
  const bar = constructBars(TRADES, CONFIG)[0];
  assert.equal(bar.thresholdTicks, 2.5);
  assert.equal(bar.buyTicks, 3);
  assert.equal(bar.sellTicks, 1);
  assert.equal(bar.dominantSide, "buy");
  assert.equal(bar.dominantCount, 3);
  assert.equal(bar.overshootTicks, 0.5);
  assert.equal(bar.lastTradeId, "W04");
});

test("run means cumulative count, not consecutive streak", () => {
  const bar = constructBars(TRADES, CONFIG)[0];
  const barSigns = SIGNS.slice(0, 4); // + + - +
  let longestStreak = 1;
  let run = 1;
  for (let i = 1; i < barSigns.length; i += 1) {
    run = barSigns[i] === barSigns[i - 1] ? run + 1 : 1;
    longestStreak = Math.max(longestStreak, run);
  }
  assert.equal(longestStreak, 2);
  assert.equal(bar.dominantCount, 3);
  assert.equal(bar.dominantCount, barSigns.filter((s) => s === 1).length);
});

test("second bar closes on dominant sell run", () => {
  const bar = constructBars(TRADES, CONFIG)[1];
  assert.equal(bar.thresholdTicks, 2.75);
  assert.equal(bar.frozenExpectedTicks, 4);
  assert.equal(bar.frozenBuyProbability, 0.6875);
  assert.equal(bar.dominantSide, "sell");
  assert.equal(bar.dominantCount, 3);
  assert.equal(bar.overshootTicks, 0.25);
});

test("threshold is symmetric in buy probability", () => {
  const buySkew = constructBars(TRADES, { ...CONFIG, initialBuyProbability: 0.8 })[0];
  const sellSkew = constructBars(TRADES, { ...CONFIG, initialBuyProbability: 0.2 })[0];
  assert.equal(buySkew.thresholdTicks, 3.2);
  assert.equal(sellSkew.thresholdTicks, 3.2);
});

test("flat trade carries sign across a bar boundary", () => {
  assert.equal(TRADES[3].price, TRADES[4].price);
  assert.equal(SIGNS[4], 1);
  assert.equal(constructBars(TRADES, CONFIG)[1].buyTicks, 1);
});

test("partial bar is not complete and does not learn", () => {
  const bars = constructBars(TRADES.slice(0, 2), CONFIG);
  assert.equal(bars.length, 1);
  assert.equal(bars[0].closeReason, "stream_end");
  assert.equal(bars[0].isComplete, false);
  assert.equal(bars[0].thresholdMet, false);
  assert.equal(bars[0].overshootTicks, 0);
  assert.equal(bars[0].frozenExpectedTicks, 4);
});

test("closePartial=false drops the tail", () => {
  assert.deepEqual(constructBars(TRADES.slice(0, 2), { ...CONFIG, closePartial: false }), []);
});

test("threshold floor applies", () => {
  const bars = constructBars(TRADES, { ...CONFIG, thresholdFloorTicks: 100 });
  assert.equal(bars.length, 1);
  assert.equal(bars[0].closeReason, "stream_end");
  assert.equal(bars[0].thresholdTicks, 100);
});

test("tie is reported when counts are equal", () => {
  const trades: Trade[] = [
    { ...TRADES[0], tradeId: "T1", price: 100 },
    { ...TRADES[1], tradeId: "T2", price: 99 },
  ];
  const bar = constructBars(trades, { ...CONFIG, thresholdFloorTicks: 50 })[0];
  assert.equal(bar.buyTicks, 1);
  assert.equal(bar.sellTicks, 1);
  assert.equal(bar.dominantSide, "tie");
});

test("empty trades returns empty", () => {
  assert.deepEqual(constructBars([], CONFIG), []);
});

test("every config key is required", () => {
  for (const key of Object.keys(CONFIG)) {
    const partial = { ...CONFIG } as Record<string, unknown>;
    delete partial[key];
    assert.throws(
      () => constructBars(TRADES, partial as unknown as Config),
      TickRunBarsValidationError,
      key,
    );
  }
});

test("rejects bad config", () => {
  const overrides: Array<Record<string, unknown>> = [
    { initialTickSign: 0 },
    { initialExpectedTicks: 0 },
    { initialBuyProbability: 1.5 },
    { initialBuyProbability: -0.1 },
    { alphaTicks: 0 },
    { alphaBuyProbability: 1.5 },
    { thresholdFloorTicks: 0 },
    { thresholdMultiplier: -1 },
    { closePartial: "yes" },
  ];
  for (const override of overrides) {
    assert.throws(
      () => constructBars(TRADES, { ...CONFIG, ...override } as unknown as Config),
      TickRunBarsValidationError,
      JSON.stringify(override),
    );
  }
});

test("equal timestamps require a sequence", () => {
  const tied: Trade[] = [TRADES[0], { ...TRADES[1], timestamp: TRADES[0].timestamp }];
  assert.throws(() => constructBars(tied, CONFIG), TickRunBarsValidationError);
  const ok: Trade[] = [
    { ...TRADES[0], sequence: 1 },
    { ...TRADES[1], timestamp: TRADES[0].timestamp, sequence: 2 },
  ];
  assert.notDeepEqual(constructBars(ok, CONFIG), []);
});

test("rejects duplicate trade id", () => {
  assert.throws(
    () => constructBars([TRADES[0], { ...TRADES[1], tradeId: "W01" }], CONFIG),
    TickRunBarsValidationError,
  );
});

test("rejects unordered trades", () => {
  assert.throws(() => constructBars([...TRADES].reverse(), CONFIG), TickRunBarsValidationError);
});

test("rejects mixed symbol", () => {
  assert.throws(
    () => constructBars([TRADES[0], { ...TRADES[1], symbol: "OTHER" }], CONFIG),
    TickRunBarsValidationError,
  );
});

test("rejects non-positive price", () => {
  assert.throws(() => constructBars([{ ...TRADES[0], price: 0 }], CONFIG), TickRunBarsValidationError);
});
