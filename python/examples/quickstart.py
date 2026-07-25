"""Quickstart: Tick-Run Bars, batch and streaming (with adaptive state).

Run:  python examples/quickstart.py
"""

from fintech_tick_run_bars import StreamingTickRunBarBuilder, construct_bars

config = {
    "closePartial": True,
    "initialTickSign": 1,
    "initialExpectedTicks": 4,
    "initialBuyProbability": 0.625,
    "alphaTicks": 0.5,
    "alphaBuyProbability": 0.5,
    "thresholdFloorTicks": 2,
    "thresholdMultiplier": 1,
}
prices = [100.00, 100.00, 99.90, 100.00, 100.00, 99.90, 99.90, 99.80]
volumes = [10, 20, 15, 25, 10, 12, 18, 14]
trades = [
    {"tradeId": f"W{i+1:02d}", "timestamp": f"2026-01-05T14:30:{i:02d}.000Z", "session": "S1",
     "symbol": "SYNTH", "price": p, "volume": v, "currency": "USD"}
    for i, (p, v) in enumerate(zip(prices, volumes))
]

# 1) Batch: a bar closes when the DOMINANT side's cumulative count hits the threshold.
for bar in construct_bars(trades, config):
    print(f"bar {bar['barIndex']}: buy={bar['buyTicks']} sell={bar['sellTicks']} "
          f"dominant={bar['dominantCount']} ({bar['dominantSide']}) "
          f"threshold={bar['thresholdTicks']} overshoot={bar['overshootTicks']} ({bar['closeReason']})")

# 2) Streaming: watch the runs build and the threshold adapt.
print("--- streaming (adaptive state) ---")
builder = StreamingTickRunBarBuilder(config)
print(f"seed: E[ticks]={builder.expected_ticks} E[p_buy]={builder.expected_buy_probability} "
      f"threshold={builder.threshold}")
for trade in trades:
    closed = builder.push(trade)
    for bar in closed:
        print(f"  closed on {trade['tradeId']}: {bar['dominantCount']} {bar['dominantSide']} ticks "
              f">= {bar['thresholdTicks']} -> new E[p_buy]={builder.expected_buy_probability:.4f} "
              f"threshold={builder.threshold:.4f}")
    if not closed:
        print(f"  {trade['tradeId']}: buy={builder.buy_ticks} sell={builder.sell_ticks} "
              f"dominant={builder.dominant_count} (threshold {builder.threshold})")
for bar in builder.flush():
    print(f"  flushed partial: ticks={bar['tickCount']} ({bar['closeReason']})")
