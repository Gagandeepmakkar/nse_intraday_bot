"""Time Machine — replay a planned NSE intraday trade before risking a rupee.

Ported from Jake Schincariol's day-trader-skill (MIT) to the NSE session. Two
independent engines over the same free 5-min data:

  1. Monte-Carlo replay: 10,000 simulated walks of the rest of the session from
     your entry, each bar drawn from the stock's OWN past bars at the same
     time-of-day (±2 slots), preserving intrabar range. Reports the odds of
     hitting target first vs stop first vs neither, and the average R.
  2. Historical replay: re-runs the SAME opening-range-breakout rule on every
     past session in the data and tallies how it actually did.

Honest by design: time-of-day-aware resampling (not one lucky path), whole-bar
OHLC draws, stop checked before target (a bar touching both = loss), only full
PAST sessions in the pool (no look-ahead), and an R-aware verdict. It is a
simulation from the last ~60 days, NOT a prediction.

    python3 timemachine.py SYMBOL SIDE ENTRY STOP TARGET [--entry-bar N] [--seed 19]
    python3 timemachine.py LAURUSLABS LONG 2386 2378 2418

NSE session: 09:15-15:30 IST = 375 min = 75 five-minute bars (slots 0..74).
"""
import sys
import random
import statistics

from analyze import _hist   # yfinance SYMBOL.NS -> IST-naive OHLCV DataFrame

SESSION_BARS = 75           # 09:15-15:30 IST, 375 min / 5
OPEN_MIN = 9 * 60 + 15      # 555
MIN_SESSIONS = 20           # refuse below this many past sessions
WINDOW = 2                  # bar-slot jitter: draw from past bar i ± WINDOW
DEFAULT_REPLAYS = 10000
DEFAULT_SEED = 19
COIN_FLIP, STRONG, MANY_NEITHER = 0.05, 0.15, 0.40
OR_BARS = 3                 # opening range = first 3 bars (15 min), matches analyze.py


def _slot(ts):
    """5-min slot index within the NSE session, or None if outside / off-grid."""
    mins = ts.hour * 60 + ts.minute
    if mins < OPEN_MIN or ts.minute % 5 != 0:
        return None
    s = (mins - OPEN_MIN) // 5
    return s if 0 <= s < SESSION_BARS else None


def _sessions(df):
    """{date: {slot: (o,h,l,c)}} for each day, oldest first."""
    out = {}
    for row in df.itertuples(index=False):
        s = _slot(row.date)
        if s is None:
            continue
        out.setdefault(row.date.date(), {})[s] = (
            float(row.open), float(row.high), float(row.low), float(row.close))
    return out


def _build_pool(sessions):
    """pool[i] = list over past sessions of bar i as (o,h,l,c)/prev_close ratios."""
    pool = [[] for _ in range(SESSION_BARS)]
    used = 0
    for day in sorted(sessions):
        D = sessions[day]
        has_any = False
        for i in range(1, SESSION_BARS):
            if i in D and (i - 1) in D:
                pc = D[i - 1][3]
                if pc > 0:
                    o, h, l, c = D[i]
                    pool[i].append((o / pc, h / pc, l / pc, c / pc))
                    has_any = True
        if has_any:
            used += 1
    return pool, used


def _montecarlo(pool, side, entry, stop, target, entry_bar, replays, seed):
    long = side == "LONG"
    risk = (entry - stop) if long else (stop - entry)
    r_target = round(((target - entry) if long else (entry - target)) / risk, 4)
    rng = random.Random(seed)

    def slot(i):
        if WINDOW == 0:
            return i
        return min(SESSION_BARS - 1, max(1, i + rng.randint(-WINDOW, WINDOW)))

    outcomes, rs, closes = [], [], []
    for _ in range(replays):
        p, outcome = entry, None
        for i in range(entry_bar + 1, SESSION_BARS):
            bucket = pool[slot(i)] or pool[i]
            if not bucket:
                continue
            o, h, l, c = rng.choice(bucket)
            hi, lo, cl = p * h, p * l, p * c
            if outcome is None:  # first touch; stop before target (conservative)
                if long:
                    if lo <= stop:
                        outcome = "stop"
                    elif hi >= target:
                        outcome = "target"
                else:
                    if hi >= stop:
                        outcome = "stop"
                    elif lo <= target:
                        outcome = "target"
            p = cl
        outcomes.append(outcome or "close")
        closes.append(p)
        if outcome == "target":
            rs.append(r_target)
        elif outcome == "stop":
            rs.append(-1.0)
        else:
            rs.append(((p - entry) if long else (entry - p)) / risk)

    n = len(outcomes)
    p_target = outcomes.count("target") / n
    p_stop = outcomes.count("stop") / n
    p_neither = outcomes.count("close") / n
    closes.sort()
    pct = lambda q: closes[min(len(closes) - 1, int(q * len(closes)))]
    return {"replays": n, "p_target": round(p_target, 4), "p_stop": round(p_stop, 4),
            "p_neither": round(p_neither, 4), "avg_R": round(statistics.mean(rs), 3),
            "r_at_target": r_target, "median_close": round(pct(0.5), 2),
            "p5_close": round(pct(0.05), 2), "p95_close": round(pct(0.95), 2)}


def _history(sessions, side, stop_rule, stop_pct, target_r):
    """Re-run the ORB rule on every past session; tally target/stop/neither."""
    long = side == "LONG"
    triggered = target = stop_c = close_c = 0
    rs = []
    for day in sorted(sessions):
        D = sessions[day]
        bars = [D[i] for i in range(SESSION_BARS) if i in D]
        if len(bars) < OR_BARS + 2:
            continue
        opening = [D[i] for i in range(OR_BARS) if i in D]
        if len(opening) < OR_BARS:
            continue
        oh = max(b[1] for b in opening)
        ol = min(b[2] for b in opening)
        # entry: first bar after the range whose close breaks it
        kk = None
        for i in range(OR_BARS, SESSION_BARS):
            if i not in D:
                continue
            c = D[i][3]
            if (long and c > oh) or (not long and c < ol):
                kk = i
                break
        if kk is None:
            continue
        e = D[kk][3]
        if stop_rule == "or_mid":
            s = (oh + ol) / 2
        elif stop_rule == "or_low":
            s = ol if long else oh
        else:
            s = e * (1 - stop_pct / 100) if long else e * (1 + stop_pct / 100)
        risk = (e - s) if long else (s - e)
        if risk <= 0:
            continue
        t = e + target_r * risk if long else e - target_r * risk
        triggered += 1
        outcome = "close"
        for i in range(kk + 1, SESSION_BARS):
            if i not in D:
                continue
            h, l = D[i][1], D[i][2]
            if (long and l <= s) or (not long and h >= s):
                outcome = "stop"; break
            if (long and h >= t) or (not long and l <= t):
                outcome = "target"; break
        if outcome == "target":
            target += 1; rs.append(target_r)
        elif outcome == "stop":
            stop_c += 1; rs.append(-1.0)
        else:
            close_c += 1
            last = D[max(i for i in range(SESSION_BARS) if i in D)][3]
            rs.append(((last - e) if long else (e - last)) / risk)
    return {"sessions": triggered, "target": target, "stop": stop_c, "neither": close_c,
            "win_rate_pct": round(target / triggered * 100, 1) if triggered else 0.0,
            "avg_R": round(statistics.mean(rs), 3) if rs else 0.0}


def _verdict(p_target, p_stop, r_target, p_neither):
    hit = p_target + p_stop
    if hit == 0:
        return "no clean outcome — almost all replays still open at the close"
    share = p_target / hit
    breakeven = 1 / (1 + r_target)   # share of wins needed to break even at this R
    edge = share - breakeven
    lean = "LONG-ish target" if edge > 0 else "stop"
    if abs(edge) < COIN_FLIP:
        base = "close to a coin flip / break-even after R"
    elif abs(edge) < STRONG:
        base = f"leans toward {'target' if edge > 0 else 'the stop'}"
    else:
        base = f"strongly favours {'the target' if edge > 0 else 'the stop'}"
    if p_neither > MANY_NEITHER:
        base += f"; but {p_neither*100:.0f}% never reach either level (time-stop risk)"
    return base


def timemachine(symbol, side, entry, stop, target, entry_bar=OR_BARS, replays=DEFAULT_REPLAYS, seed=DEFAULT_SEED):
    side = side.upper()
    entry, stop, target = float(entry), float(stop), float(target)
    long = side == "LONG"
    if (long and not (stop < entry < target)) or (not long and not (target < entry < stop)):
        return {"symbol": symbol, "error": f"bad levels for {side}: need "
                + ("stop<entry<target" if long else "target<entry<stop")}
    df = _hist(symbol.upper(), "60d", "5m")
    if df.empty:
        return {"symbol": symbol, "error": "no 5-min data"}
    # pool = sessions strictly before the latest day (no look-ahead on today)
    sessions = _sessions(df)
    if sessions:
        latest = max(sessions)
        sessions = {d: v for d, v in sessions.items() if d < latest}
    if len(sessions) < MIN_SESSIONS:
        return {"symbol": symbol, "error": f"only {len(sessions)} past sessions (need {MIN_SESSIONS})"}
    pool, used = _build_pool(sessions)
    mc = _montecarlo(pool, side, entry, stop, target, entry_bar, replays, seed)
    risk = (entry - stop) if long else (stop - entry)
    stop_pct = 100 * risk / entry
    hist = _history(sessions, side, "pct", stop_pct, mc["r_at_target"])
    return {"symbol": symbol.upper(), "side": side, "entry": entry, "stop": stop,
            "target": target, "pool_sessions": used, "montecarlo": mc, "history": hist,
            "verdict": _verdict(mc["p_target"], mc["p_stop"], mc["r_at_target"], mc["p_neither"]),
            "note": "Simulation from ~60 days of this stock's own 5-min bars. NOT a prediction."}


def _cli():
    a = sys.argv[1:]
    if len(a) < 5:
        print(__doc__); return
    symbol, side, entry, stop, tgt = a[0], a[1], a[2], a[3], a[4]
    entry_bar = int(a[a.index("--entry-bar") + 1]) if "--entry-bar" in a else OR_BARS
    seed = int(a[a.index("--seed") + 1]) if "--seed" in a else DEFAULT_SEED
    import json
    print(json.dumps(timemachine(symbol, side, entry, stop, tgt, entry_bar, seed=seed), indent=2))


if __name__ == "__main__":
    _cli()
