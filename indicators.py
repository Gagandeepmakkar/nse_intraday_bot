"""Pure technical-indicator functions over a 5-minute OHLCV DataFrame.

Every function expects a pandas DataFrame with columns:
    date (tz-naive datetime), open, high, low, close, volume
sorted ascending by date. Functions return plain floats/dicts; they never call a
data source, so they are deterministic and unit-testable offline.
"""
import numpy as np
import pandas as pd


def prepare(df):
    """Normalize candle types and sort. Returns a fresh DataFrame."""
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.tz_localize(None)
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)


def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def session_vwap(df):
    """Volume-weighted average price over the latest session only."""
    last_day = df["date"].iloc[-1].date()
    today = df[df["date"].dt.date == last_day]
    if today.empty:
        today = df.tail(20)
    typical = (today["high"] + today["low"] + today["close"]) / 3
    vol = float(today["volume"].sum())
    return float((typical * today["volume"]).sum() / vol) if vol > 0 else float(today["close"].iloc[-1])


def atr(df, period=14):
    """Average True Range computed on the given (5-minute) candles."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([(high - low).abs(),
                    (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    if len(tr.dropna()) < period:
        return float(tr.dropna().mean()) if len(tr.dropna()) else 0.0
    return float(tr.ewm(span=period, adjust=False).mean().iloc[-1])


def time_adjusted_rvol(df, window_sessions=10):
    """Cumulative volume today up to the latest candle's clock time, divided by the
    average cumulative volume up to the same clock time over prior sessions.

    Uses at most ``window_sessions`` of the most recent comparable sessions for the
    baseline. Requires at least 2 prior sessions; otherwise falls back to a simple
    recent-candle ratio. Returns (rvol, sessions_used); sessions_used is 0 on the
    fallback path so callers can disclose the shorter/weaker baseline.
    """
    df = df.copy()
    df["day"] = df["date"].dt.date
    df["clock"] = df["date"].dt.strftime("%H:%M")
    last_day = df["day"].iloc[-1]
    cutoff = df["clock"].iloc[-1]

    def cum_to_cutoff(group):
        return group[group["clock"] <= cutoff]["volume"].sum()

    today_cum = cum_to_cutoff(df[df["day"] == last_day])
    prior = [cum_to_cutoff(g) for day, g in df[df["day"] != last_day].groupby("day")]
    prior = [v for v in prior if v > 0]
    if len(prior) >= 2:
        window = prior[-max(window_sessions, 2):]
        baseline = float(np.mean(window))
        if baseline > 0:
            return float(today_cum / baseline), len(window)
    # Fallback: latest candle vs trailing 19-candle average.
    recent = df.tail(20)
    avg = float(recent["volume"].iloc[:-1].mean()) if len(recent) > 1 else 0.0
    return (float(df["volume"].iloc[-1] / avg) if avg > 0 else 0.0), 0


def prev_day_hlc(df):
    """High/low/close of the most recent completed prior session, or None."""
    days = sorted(df["date"].dt.date.unique())
    if len(days) < 2:
        return None
    prev = df[df["date"].dt.date == days[-2]]
    if prev.empty:
        return None
    return {"high": float(prev["high"].max()),
            "low": float(prev["low"].min()),
            "close": float(prev["close"].iloc[-1])}


def central_pivot_range(hlc):
    """CPR + Camarilla H3/H4/L3/L4 from prior-session H/L/C."""
    h, l, c = hlc["high"], hlc["low"], hlc["close"]
    pivot = (h + l + c) / 3
    bc = (h + l) / 2
    tc = 2 * pivot - bc
    rng = h - l
    return {
        "pivot": pivot,
        "cpr_top": max(tc, bc),
        "cpr_bottom": min(tc, bc),
        "cam_h4": c + rng * 1.1 / 2,
        "cam_h3": c + rng * 1.1 / 4,
        "cam_l3": c - rng * 1.1 / 4,
        "cam_l4": c - rng * 1.1 / 2,
    }


def rsi(closes, period=14):
    """Wilder RSI over a list/Series of closes. None if too few; 100 if no losses.
    Seeds with the simple mean of the first `period`, then Wilder-smooths."""
    c = list(closes)
    if len(c) <= period:
        return None
    gains = [max(c[i] - c[i - 1], 0.0) for i in range(1, len(c))]
    losses = [max(c[i - 1] - c[i], 0.0) for i in range(1, len(c))]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return float(100 - 100 / (1 + ag / al))


def daily_setups(closes):
    """Swing setups over a list of DAILY closes (oldest first, >=30 bars). Returns
    the list of matched tags (a symbol can match several). Ported from the
    day-trader-skill scanner. Needs daily closes only — no intraday, no OHLC.

      breakout_20d    last close > every one of the prior 20 closes
      reclaim_ma20    prev close < MA20(prev), last close > MA20 (cross back up)
      bull_flag       >10% run over the 10 sessions before the last 5, then the
                      last 5 closes inside a 5% band (flagpole + tight flag)
      oversold        Wilder RSI-14 < 30
      oversold_bounce RSI-14 was < 30 yesterday AND today closed up
    """
    c = list(closes)
    if len(c) < 30:
        return []
    last = c[-1]
    hi20 = max(c[-21:-1])
    ma20 = sum(c[-20:]) / 20
    ma20_prev = sum(c[-21:-1]) / 20
    run = c[-6] / c[-16] - 1 if c[-16] else 0.0
    rng5 = (max(c[-5:]) - min(c[-5:])) / last if last else 1.0
    r, r_prev = rsi(c), rsi(c[:-1])
    tags = []
    if last > hi20:
        tags.append("breakout_20d")
    if run > 0.10 and rng5 < 0.05:
        tags.append("bull_flag")
    if c[-2] < ma20_prev and last > ma20:
        tags.append("reclaim_ma20")
    if r is not None and r < 30:
        tags.append("oversold")
    if r_prev is not None and r_prev < 30 and last > c[-2]:
        tags.append("oversold_bounce")
    return tags


def opening_range(df, candles=3):
    """High/low of the first N candles of the latest session (default 15 min)."""
    last_day = df["date"].iloc[-1].date()
    today = df[df["date"].dt.date == last_day]
    if today.empty:
        return None
    opening = today.head(candles)
    return {"high": float(opening["high"].max()), "low": float(opening["low"].min())}
