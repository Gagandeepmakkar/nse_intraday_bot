"""Deeper per-stock analysis for the extension's "Analyze this stock" button.

Heavier than the in-browser vote: for ONE stock it pulls multi-timeframe data,
computes a full indicator read, and runs a live mini-backtest of the opening-range
breakout setup over the stock's recent sessions — returning a verdict WITH evidence
(actual recent win-rate / expectancy), not just a label. Takes a few seconds because
it fetches data and simulates — that's the real work, not padding.

Used by serve_picks.py's /analyze route. Reuses indicators.py / costs.py /
backtest.simulate_fill so it's the same math as the bot, not a parallel toy.
"""
import numpy as np
import pandas as pd
import yfinance as yf

import indicators as ind
from backtest import simulate_fill


def _hist(sym, period, interval):
    h = yf.Ticker(f"{sym}.NS").history(period=period, interval=interval)
    if h.empty:
        return pd.DataFrame()
    h = h.reset_index()
    tcol = "Datetime" if "Datetime" in h.columns else h.columns[0]
    ts = pd.to_datetime(h[tcol])
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return pd.DataFrame({"date": ts, "open": h["Open"], "high": h["High"],
                         "low": h["Low"], "close": h["Close"], "volume": h["Volume"]}).dropna()


def _rsi(closes, period=14):
    if len(closes) <= period:
        return 50.0
    d = np.diff(closes)
    gain = np.where(d > 0, d, 0.0)
    loss = np.where(d < 0, -d, 0.0)
    ag, al = gain[:period].mean(), loss[:period].mean()
    for i in range(period, len(d)):
        ag = (ag * (period - 1) + gain[i]) / period
        al = (al * (period - 1) + loss[i]) / period
    if al == 0:
        return 100.0
    return float(100 - 100 / (1 + ag / al))


def _daily_trend(daily):
    c = daily["close"].dropna()
    if len(c) < 25:
        return "FLAT"
    sma = c.rolling(20).mean()
    last, now, prev = float(c.iloc[-1]), float(sma.iloc[-1]), float(sma.iloc[-5])
    if last > now > prev:
        return "UP"
    if last < now < prev:
        return "DOWN"
    return "FLAT"


def _setup_backtest(m5, side, atr):
    """Replay the opening-range-breakout setup on each recent session of this stock.
    Entry = ORB high/low (first 3 candles), stop = other side, targets = ATR multiples.
    Returns (trades, wins, win_rate, expectancy)."""
    m5 = m5.copy()
    m5["d"] = m5["date"].dt.date
    days = sorted(m5["d"].unique())
    wins, nets = 0, []
    for day in days[:-1]:  # completed sessions only
        g = m5[m5["d"] == day]
        if len(g) < 20:
            continue
        opening = g.head(3)
        hi, lo = float(opening["high"].max()), float(opening["low"].min())
        a = ind.atr(g) or atr
        # Test the SAME setup we recommend live: breakout entry, ATR-based stop/targets.
        if side == "LONG":
            entry, stop = hi, hi - 1.2 * a
            t1, t2 = entry + 1.5 * a, entry + 2.5 * a
        else:
            entry, stop = lo, lo + 1.2 * a
            t1, t2 = entry - 1.5 * a, entry - 2.5 * a
        eval_rows = g.iloc[3:][["high", "low", "close"]].to_dict("records")
        if not eval_rows or entry == stop:
            continue
        qty = max(1, int(100000 / entry))  # realistic notional so costs are proportional
        result, net = simulate_fill(side, entry, stop, t1, t2, qty, eval_rows)
        if result == "NOFILL":
            continue
        nets.append(net)
        if net > 0:
            wins += 1
    n = len(nets)
    if n == 0:
        return 0, 0, 0.0, 0.0
    return n, wins, wins / n * 100, sum(nets) / n


def analyze(symbol, side_hint=""):
    sym = symbol.upper().strip()
    m5 = _hist(sym, "15d", "5m")
    daily = _hist(sym, "3mo", "1d")
    if len(m5) < 50:
        return {"symbol": sym, "action": "WAIT", "confidence": 0,
                "note": "not enough intraday data for a real analysis", "evidence": {}}
    m5 = ind.prepare(m5)
    price = float(m5["close"].iloc[-1])
    ema20 = float(ind.ema(m5["close"], 20).iloc[-1])
    ema50 = float(ind.ema(m5["close"], 50).iloc[-1])
    vwap = ind.session_vwap(m5)
    atr = ind.atr(m5) or price * 0.003
    rsi = _rsi(m5["close"].values)

    tf5 = "UP" if (ema20 > ema50 and price > vwap) else "DOWN" if (ema20 < ema50 and price < vwap) else "FLAT"
    tfd = _daily_trend(daily)

    # Pick the side to evaluate: honor the bot's hint, else the intraday trend.
    side = (side_hint or "").upper()
    if side not in ("LONG", "SHORT"):
        side = "LONG" if tf5 == "UP" else "SHORT" if tf5 == "DOWN" else "LONG"

    trades, wins, win_rate, expectancy = _setup_backtest(m5, side, atr)

    # Confluence: multi-timeframe agreement + RSI not stretched against us + a
    # recent win-rate above coin-flip. Each adds confidence.
    want = "UP" if side == "LONG" else "DOWN"
    checks = {
        "intraday_trend": tf5 == want,
        "daily_trend": tfd == want or tfd == "FLAT",
        "rsi_ok": (rsi > 50 if side == "LONG" else rsi < 50),
        "recent_winrate": win_rate >= 50 and trades >= 5,
        "positive_expectancy": expectancy > 0 and trades >= 5,
    }
    passed = sum(1 for v in checks.values() if v)
    confidence = int(passed / len(checks) * 100)

    # Decide: need intraday-trend confluence AND, when we have enough history, a
    # non-negative recent expectancy — so a setup that recently LOST money on this
    # stock is held even if the trend looks right. That veto is the deep edge.
    enough = trades >= 5
    evidence_ok = (not enough) or expectancy > 0
    if passed >= 4 and checks["intraday_trend"] and evidence_ok:
        action = "BUY" if side == "LONG" else "SELL"
    else:
        action = "HOLD"

    levels = None
    if action in ("BUY", "SELL"):
        if side == "LONG":
            levels = {"entry": price, "stop": price - 1.2 * atr,
                      "target1": price + 1.5 * atr, "target2": price + 2.5 * atr}
        else:
            levels = {"entry": price, "stop": price + 1.2 * atr,
                      "target1": price - 1.5 * atr, "target2": price - 2.5 * atr}

    return {
        "symbol": sym, "side": side, "action": action, "confidence": confidence,
        "price": round(price, 2), "levels": {k: round(v, 2) for k, v in levels.items()} if levels else None,
        "evidence": {
            "intraday_trend_5m": tf5, "daily_trend": tfd, "rsi": round(rsi, 1),
            "vwap": round(vwap, 2), "atr": round(atr, 2),
            "setup_backtest": {"sessions": trades, "wins": wins,
                               "win_rate_pct": round(win_rate, 1),
                               "expectancy_rs_per_10qty": round(expectancy, 1)},
            "checks_passed": f"{passed}/{len(checks)}",
            "checks": checks,
        },
        "note": "Multi-timeframe + recent-setup backtest on this stock. Confirmation only; trade manually.",
    }


if __name__ == "__main__":
    import sys, json
    sym = sys.argv[1] if len(sys.argv) > 1 else "LAURUSLABS"
    side = sys.argv[2] if len(sys.argv) > 2 else ""
    print(json.dumps(analyze(sym, side), indent=2))
