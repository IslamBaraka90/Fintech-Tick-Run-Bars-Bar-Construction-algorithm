import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

import { TickRunBarsValidationError, constructBars, type Config, type Trade } from "../src/bars.ts";
import { StreamingTickRunBarBuilder } from "../src/streaming.ts";
import { loadTrades } from "../src/tape.ts";

const FIXTURE = JSON.parse(
  readFileSync(fileURLToPath(new URL("./fixtures/worked_example.json", import.meta.url)), "utf8"),
);
const CONFIG: Config = FIXTURE.config;
const TRADES: Trade[] = FIXTURE.trades;
const TAPE_PATH = fileURLToPath(new URL("./fixtures/trade_tape.csv", import.meta.url));

function stream(trades: Trade[], config: Config) {
  const builder = new StreamingTickRunBarBuilder(config);
  const emitted = builder.pushMany(trades);
  emitted.push(...builder.flush());
  return emitted;
}

test("streaming matches batch", () => {
  assert.deepEqual(stream(TRADES, CONFIG), constructBars(TRADES, CONFIG));
});

test("streaming matches batch (closePartial=false)", () => {
  const config = { ...CONFIG, closePartial: false };
  assert.deepEqual(stream(TRADES.slice(0, 2), config), constructBars(TRADES.slice(0, 2), config));
});

test("streaming matches batch over the tape", () => {
  const tape = loadTrades(TAPE_PATH);
  assert.deepEqual(stream(tape, CONFIG), constructBars(tape, CONFIG));
});

test("state is observable and seeded", () => {
  const builder = new StreamingTickRunBarBuilder(CONFIG);
  assert.equal(builder.expectedTicks, 4);
  assert.equal(builder.expectedBuyProbability, 0.625);
  assert.equal(builder.threshold, 2.5);
  assert.equal(builder.buyTicks, 0);
  assert.equal(builder.sellTicks, 0);
  assert.equal(builder.dominantCount, 0);
});

test("counts track the documented signs", () => {
  const builder = new StreamingTickRunBarBuilder(CONFIG);
  const expected: Array<[number, number]> = [
    [1, 0],
    [2, 0],
    [2, 1],
  ];
  expected.forEach((pair, index) => {
    assert.deepEqual(builder.push(TRADES[index]), []);
    assert.deepEqual([builder.buyTicks, builder.sellTicks], pair);
  });
  const closed = builder.push(TRADES[3]);
  assert.equal(closed.length, 1);
  assert.equal(closed[0].dominantCount, 3);
});

test("state updates after a threshold close", () => {
  const builder = new StreamingTickRunBarBuilder(CONFIG);
  builder.pushMany(TRADES.slice(0, 4));
  assert.equal(builder.expectedTicks, 4);
  assert.equal(builder.expectedBuyProbability, 0.6875);
  assert.equal(builder.threshold, 2.75);
});

test("dominant count is the max, not the net", () => {
  const builder = new StreamingTickRunBarBuilder(CONFIG);
  builder.pushMany(TRADES.slice(0, 3)); // + + -
  assert.equal(builder.buyTicks, 2);
  assert.equal(builder.sellTicks, 1);
  assert.equal(builder.dominantCount, 2); // not the net imbalance of 1
});

test("tick sign carries across the bar boundary", () => {
  const builder = new StreamingTickRunBarBuilder(CONFIG);
  builder.pushMany(TRADES.slice(0, 4));
  assert.equal(builder.tickSign, 1);
  builder.push(TRADES[4]); // flat -> carries +1 into the new bar
  assert.equal(builder.buyTicks, 1);
});

test("flush closes the partial tail", () => {
  const builder = new StreamingTickRunBarBuilder(CONFIG);
  builder.pushMany(TRADES.slice(0, 2));
  const final = builder.flush();
  assert.equal(final.length, 1);
  assert.equal(final[0].closeReason, "stream_end");
});

test("cannot push after flush", () => {
  const builder = new StreamingTickRunBarBuilder(CONFIG);
  builder.pushMany(TRADES);
  builder.flush();
  assert.throws(() => builder.push(TRADES[0]), TickRunBarsValidationError);
});

test("streaming validates incrementally", () => {
  const builder = new StreamingTickRunBarBuilder(CONFIG);
  builder.push(TRADES[0]);
  assert.throws(() => builder.push({ ...TRADES[1], tradeId: "W01" }), TickRunBarsValidationError);
});
