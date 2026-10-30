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


def opening_range(df, candles=3):
    """High/low of the first N candles of the latest session (default 15 min)."""
    last_day = df["date"].iloc[-1].date()
    today = df[df["date"].dt.date == last_day]
    if today.empty:
        return None
    opening = today.head(candles)
    return {"high": float(opening["high"].max()), "low": float(opening["low"].min())}
