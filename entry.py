"""Bar-by-bar entry timer for NSE intraday — waiting / triggered / expired.

Ported from the day-trader-skill entry timer. The correctness win over "is the
live price above the level" is that it fires only on a COMPLETED 5-min bar whose
CLOSE clears the opening-range level (strictly greater), skips the still-forming
bar, and expires after the entry cutoff. One trigger per session (first close wins).

    python3 entry.py SYMBOL LONG            # status now, using today's 5-min bars
    python3 entry.py SYMBOL SHORT --or 15

Pure logic lives in evaluate(bars, side, now_min, ...) so it is unit-testable and
replayable offline with no network.
"""
import sys

from analyze import _hist

OPEN_MIN = 9 * 60 + 15          # 09:15 IST
CLOSE_MIN = 15 * 60 + 30        # 15:30 IST
SESSION_BARS = 75


def evaluate(bars, side, now_min, or_minutes=15, no_entries_after_min=None):
    """bars: completed bars [{slot,o,h,l,c}] in slot order. now_min/cutoff are
    minutes-from-open. Returns {status, level, reason, ...}. side = LONG/SHORT."""
    long = side.upper() == "LONG"
    n_or = or_minutes // 5
    if no_entries_after_min is None:
        no_entries_after_min = (15 * 60) - OPEN_MIN   # 15:00 IST default cutoff

    if now_min < 0:
        return {"status": "waiting", "reason": "pre-open"}
    or_bars = [b for b in bars if b["slot"] < n_or]
    if now_min < n_or * 5 or len(or_bars) < n_or:
        return {"status": "waiting", "reason": f"opening range still forming ({or_minutes}m)"}

    or_high = max(b["h"] for b in or_bars)
    or_low = min(b["l"] for b in or_bars)
    level = or_high if long else or_low

    for b in bars:
        if b["slot"] < n_or:
            continue
        bar_end = (b["slot"] + 1) * 5
        if bar_end > no_entries_after_min:
            break  # past the entry cutoff — no late chases
        if (long and b["c"] > or_high) or (not long and b["c"] < or_low):
            return {"status": "triggered", "level": round(level, 2),
                    "trigger_bar": b["slot"], "trigger_close": round(b["c"], 2),
                    "or_high": round(or_high, 2), "or_low": round(or_low, 2),
                    "reason": f"5-min close {b['c']:.2f} broke the {'high' if long else 'low'} {level:.2f}"}

    if now_min >= no_entries_after_min:
        return {"status": "expired", "level": round(level, 2),
                "reason": "no breakout close before the entry cutoff"}
    last = bars[-1]["c"] if bars else None
    dist = (level - last) if long else (last - level) if last else None
    return {"status": "waiting", "level": round(level, 2),
            "or_high": round(or_high, 2), "or_low": round(or_low, 2),
            "reason": f"price {last:.2f} not yet past {level:.2f}" if last else "no bars yet"}


def _closed_bars(df, now_min):
    """Today's completed, grid-aligned 5-min bars as [{slot,o,h,l,c}]."""
    if df.empty:
        return []
    last_day = df["date"].iloc[-1].date()
    out = []
    for row in df.itertuples(index=False):
        if row.date.date() != last_day or row.date.minute % 5 != 0:
            continue
        mins = row.date.hour * 60 + row.date.minute
        slot = (mins - OPEN_MIN) // 5
        if not (0 <= slot < SESSION_BARS):
            continue
        if (slot + 1) * 5 > now_min:   # bar not closed yet
            continue
        out.append({"slot": slot, "o": float(row.open), "h": float(row.high),
                    "l": float(row.low), "c": float(row.close)})
    return out


def entry_status(symbol, side, now=None):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Kolkata")
    n = now or datetime.now(tz).replace(tzinfo=None)
    now_min = n.hour * 60 + n.minute - OPEN_MIN
    df = _hist(symbol.upper(), "5d", "5m")
    bars = _closed_bars(df, now_min)
    res = evaluate(bars, side, now_min)
    res["symbol"] = symbol.upper()
    res["side"] = side.upper()
    return res


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 2:
        print("usage: python entry.py SYMBOL LONG|SHORT [--or 15]")
        sys.exit(0)
    import json
    print(json.dumps(entry_status(a[0], a[1]), indent=2))
