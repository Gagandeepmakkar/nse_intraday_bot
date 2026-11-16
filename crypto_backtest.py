"""Rigorous trend-following backtest on BTC/ETH daily (8 years). Tests the best-evidenced
systematic crypto edge — time-series momentum / trend-following — long & short, with ATR
trailing stops, against buy-and-hold, at two cost levels (0.1% international, 1% India TDS).
No prediction: pure rules, honest costs, full-period + out-of-sample style metrics."""
import json
import math
from pathlib import Path

DATA = Path("/tmp/crypto")


def load(sym):
    return json.loads((DATA / f"{sym}.json").read_text())


def atr(c, i, p=14):
    s = 0
    for k in range(max(1, i - p + 1), i + 1):
        s += max(c[k]["h"] - c[k]["l"], abs(c[k]["h"] - c[k - 1]["c"]), abs(c[k]["l"] - c[k - 1]["c"]))
    return s / min(p, i)


def sma(c, i, p):
    return sum(c[k]["c"] for k in range(i - p + 1, i + 1)) / p


def run(c, strat, allow_short, cost):
    """Return equity multiple, CAGR, Sharpe, maxDD, trades, win%. Position sizing = all-in
    (1x), daily rebalanced to the signal; realistic for a single-asset trend system."""
    n = len(c)
    eq = 1.0
    pos = 0          # +1 long, -1 short, 0 flat
    entry = 0.0
    trailing = None
    trades, wins = 0, 0
    peak, mdd = 1.0, 0.0
    rets = []
    for i in range(210, n):
        px = c[i]["c"]
        prev = c[i - 1]["c"]
        # mark-to-market the open position on today's move
        if pos != 0:
            eq *= 1 + pos * (px / prev - 1)
        peak = max(peak, eq); mdd = min(mdd, eq / peak - 1)
        rets.append(pos * (px / prev - 1) if pos else 0.0)

        # --- signal ---
        want = 0
        if strat == "donchian":
            hi = max(c[k]["h"] for k in range(i - 20, i))
            lo = min(c[k]["l"] for k in range(i - 20, i))
            if px > hi: want = 1
            elif px < lo: want = -1 if allow_short else 0
            else: want = pos  # hold until opposite break
        elif strat == "sma50":
            want = 1 if px > sma(c, i, 50) else (-1 if allow_short else 0)
        elif strat == "sma100":
            want = 1 if px > sma(c, i, 100) else (-1 if allow_short else 0)
        elif strat == "dual":  # 20>50 fast/slow SMA trend
            f, s = sma(c, i, 20), sma(c, i, 50)
            want = 1 if f > s else (-1 if allow_short else 0)

        # ATR trailing stop overrides (chandelier)
        if pos == 1:
            trailing = max(trailing or -1e18, max(c[k]["h"] for k in range(i - 21, i + 1)) - 3 * atr(c, i))
            if px < trailing: want = 0
        elif pos == -1:
            trailing = min(trailing or 1e18, min(c[k]["l"] for k in range(i - 21, i + 1)) + 3 * atr(c, i))
            if px > trailing: want = 0

        if want != pos:
            if pos != 0:  # closing
                trades += 1
                if pos * (px - entry) > 0: wins += 1
                eq *= 1 - cost
            if want != 0:  # opening
                eq *= 1 - cost
                entry = px; trailing = None
            pos = want

    years = (c[-1]["t"] - c[210]["t"]) / 31557600
    cagr = eq ** (1 / years) - 1 if eq > 0 and years > 0 else -1
    mean = sum(rets) / len(rets); var = sum((r - mean) ** 2 for r in rets) / len(rets)
    sharpe = (mean / math.sqrt(var)) * math.sqrt(365) if var > 0 else 0
    return dict(eq=eq, cagr=cagr, sharpe=sharpe, mdd=mdd, trades=trades, win=wins / trades if trades else 0)


def bh(c):
    e = c[-1]["c"] / c[210]["c"]
    years = (c[-1]["t"] - c[210]["t"]) / 31557600
    peak, mdd = 0, 0
    for x in c[210:]:
        peak = max(peak, x["c"]); mdd = min(mdd, x["c"] / peak - 1)
    return dict(eq=e, cagr=e ** (1 / years) - 1, mdd=mdd)


for sym in ["BTC-USD", "ETH-USD"]:
    c = load(sym)
    b = bh(c)
    print(f"\n===== {sym}  ({len(c)} days ~8y) =====")
    print(f"Buy & hold: {b['eq']:.1f}x | CAGR {b['cagr']*100:.0f}% | maxDD {b['mdd']*100:.0f}%")
    print(f"{'strategy':<22} {'side':<5} {'cost':<6} | {'mult':>7} {'CAGR':>6} {'Shrp':>5} {'maxDD':>6} {'trades':>6} {'win%':>5}")
    for strat in ["donchian", "sma50", "sma100", "dual"]:
        for short_label, allow in [("L", False), ("L/S", True)]:
            for cl, cost in [("0.1%", 0.001), ("1%TDS", 0.01)]:
                r = run(c, strat, allow, cost)
                print(f"{strat:<22} {short_label:<5} {cl:<6} | {r['eq']:>6.1f}x {r['cagr']*100:>5.0f}% {r['sharpe']:>5.2f} {r['mdd']*100:>5.0f}% {r['trades']:>6} {r['win']*100:>4.0f}%")
