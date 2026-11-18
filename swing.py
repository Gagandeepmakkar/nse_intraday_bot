"""Unified SWING scoring brain (daily timeframe) — the single source of truth.

One stock in -> one verdict out: direction, 0-100 score, entry / stop / target1 /
target2, confidence, and the per-factor breakdown (so every call is explainable).
Mail scans, and later the extension, all read THIS, so results never diverge.

Why swing/daily: every edge test in this project (intraday scanner, early-RVOL) and
the crypto work showed intraday bleeds costs; the higher timeframe is where any edge
lives. Fewer, cleaner setups.

Deterministic: each factor adds points; AI only reads catalyst text (passed in). Data
is free Upstox daily candles + NIFTY for relative strength + oi.py option walls.

NOT a prediction. It is a ranked, framed setup with a stop. Paper first.
"""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

import oi

TZ = ZoneInfo("Asia/Kolkata")
V3 = "https://api.upstox.com/v3"
NIFTY_KEY = "NSE_INDEX|Nifty 50"


# --------------------------------------------------------------------------- #
# data (free Upstox)
# --------------------------------------------------------------------------- #
def _daily(ik, days=320):
    """Ascending daily OHLCV DataFrame for an instrument_key, or empty."""
    to = datetime.now(TZ).strftime("%Y-%m-%d")
    frm = (datetime.now(TZ) - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        r = requests.get(f"{V3}/historical-candle/{ik}/days/1/{to}/{frm}",
                         headers=oi._headers(), timeout=20)
        candles = (r.json().get("data") or {}).get("candles") or []
    except Exception:
        return pd.DataFrame()
    if not candles:
        return pd.DataFrame()
    rows = list(reversed(candles))  # API returns newest-first
    df = pd.DataFrame([{"date": pd.to_datetime(c[0][:19]), "open": c[1], "high": c[2],
                        "low": c[3], "close": c[4], "volume": c[5]} for c in rows])
    return df


def _ltp(ik):
    try:
        r = requests.get("https://api.upstox.com/v2/market-quote/ltp",
                         params={"instrument_key": ik}, headers=oi._headers(), timeout=15)
        d = (r.json().get("data") or {})
        return float(list(d.values())[0]["last_price"]) if d else None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# indicators (pure)
# --------------------------------------------------------------------------- #
def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def _rsi(closes, n=14):
    d = closes.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, 1e-9)
    return float((100 - 100 / (1 + rs)).iloc[-1])


def _atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l).abs(), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return float(tr.ewm(alpha=1 / n, adjust=False).mean().iloc[-1])


def _round_levels(price):
    step = 100.0 if price >= 5000 else 50.0 if price >= 1000 else 10.0 if price >= 300 else 5.0 if price >= 100 else 1.0
    base = round(price / step) * step
    return [base + k * step for k in range(-2, 3) if base + k * step > 0]


def _swing_levels(df, price):
    """Daily-timeframe structural ladder: 20/50-day extremes, recent swing H/L,
    round numbers. Returns sorted [{price,label}]."""
    raw = []
    raw += [(float(df["high"].tail(20).max()), "20d-high"), (float(df["low"].tail(20).min()), "20d-low")]
    raw += [(float(df["high"].tail(50).max()), "50d-high"), (float(df["low"].tail(50).min()), "50d-low")]
    raw += [(float(df["high"].iloc[-11:-1].max()), "swing-high"), (float(df["low"].iloc[-11:-1].min()), "swing-low")]
    raw.append((float(df["close"].iloc[-1]), "prev-close"))
    raw += [(lv, "round") for lv in _round_levels(price)]
    raw = [(round(p, 2), lab) for p, lab in raw if p and p > 0]
    raw.sort(key=lambda x: x[0])
    merged = []
    for p, lab in raw:
        if merged and abs(p - merged[-1]["price"]) <= merged[-1]["price"] * 0.4 / 100:
            if lab not in merged[-1]["label"]:
                merged[-1]["label"] += "+" + lab
            continue
        merged.append({"price": p, "label": lab})
    return merged


# --------------------------------------------------------------------------- #
# the brain
# --------------------------------------------------------------------------- #
_NIFTY_CACHE = {}


def _nifty_ret(days=20):
    if "df" not in _NIFTY_CACHE:
        _NIFTY_CACHE["df"] = _daily(NIFTY_KEY, 120)
    df = _NIFTY_CACHE["df"]
    if df.empty or len(df) < days + 1:
        return None, "FLAT"
    c = df["close"]
    ret = (c.iloc[-1] / c.iloc[-days] - 1) * 100
    regime = "UP" if c.iloc[-1] > _ema(c, 20).iloc[-1] > _ema(c, 50).iloc[-1] else \
             "DOWN" if c.iloc[-1] < _ema(c, 20).iloc[-1] < _ema(c, 50).iloc[-1] else "FLAT"
    return float(ret), regime


def score(symbol, catalyst=None, fast=False):
    """Full swing verdict for one stock. catalyst (optional) =
    {'direction':'up'|'down','tier':1|2|3} from the AI filing read, to layer in news.
    fast=True skips the live-LTP and OI calls (1 API call total) for bulk scans; the
    top picks are then re-scored with fast=False for live price + option walls."""
    sym = symbol.upper().strip()
    ik = oi.instrument_key(sym)
    if not ik:
        return {"symbol": sym, "action": "WAIT", "note": "symbol not found on Upstox"}
    df = _daily(ik)
    if df.empty or len(df) < 60:
        return {"symbol": sym, "action": "WAIT", "note": "not enough daily history"}

    c = df["close"]
    price = float(c.iloc[-1]) if fast else (_ltp(ik) or float(c.iloc[-1]))
    ema20, ema50 = float(_ema(c, 20).iloc[-1]), float(_ema(c, 50).iloc[-1])
    sma200 = float(c.rolling(200).mean().iloc[-1]) if len(c) >= 200 else float(c.rolling(min(len(c), 100)).mean().iloc[-1])
    rsi = _rsi(c)
    atr = _atr(df) or price * 0.02
    rvol = float(df["volume"].iloc[-1] / df["volume"].tail(20).mean()) if df["volume"].tail(20).mean() > 0 else 0
    turnover_cr = float(price * df["volume"].tail(5).mean()) / 1e7
    stk_ret = (price / float(c.iloc[-20]) - 1) * 100
    nret, regime = _nifty_ret(20)
    rs = round(stk_ret - nret, 2) if nret is not None else None
    walls = None
    if not fast:
        try:
            walls = oi.walls(sym)
        except Exception:
            walls = None

    levels = _swing_levels(df, price)
    res = [l for l in levels if l["price"] >= price + 0.4 * atr]
    sup = [l for l in levels if l["price"] <= price - 0.4 * atr]
    nearest_res = res[0] if res else None
    nearest_sup = list(reversed(sup))[0] if sup else None

    # ---- score a LONG thesis and a SHORT thesis; take the stronger ----
    def trend_pts(long):
        if long:
            return 25 if (price > ema20 > ema50 and price > sma200) else 15 if (price > ema20 and ema20 > ema50) else 5 if price > ema20 else 0
        return 25 if (price < ema20 < ema50 and price < sma200) else 15 if (price < ema20 and ema20 < ema50) else 5 if price < ema20 else 0

    def rs_pts(long):
        if rs is None:
            return 5
        return (12 if rs > 1 else 8 if rs > 0 else 0) if long else (12 if rs < -1 else 8 if rs < 0 else 0)

    def rsi_pts(long):
        if long:
            return 12 if 50 <= rsi <= 68 else 8 if 45 <= rsi < 50 else -12 if rsi > 78 else 0
        return 12 if 32 <= rsi <= 50 else 8 if 50 < rsi <= 55 else -12 if rsi < 22 else 0

    def regime_pts(long):
        return 12 if regime == ("UP" if long else "DOWN") else -8 if regime == ("DOWN" if long else "UP") else 0

    def vol_pts():
        return 10 if rvol >= 1.5 else 6 if rvol >= 1.1 else 0

    def oi_pts(long):
        if not (walls and walls.get("call_wall") and walls.get("put_wall")):
            return 5  # no F&O -> neutral
        if long:
            return 10 if price < walls["call_wall"] - 1.5 * atr and price > walls["put_wall"] else 3
        return 10 if price > walls["put_wall"] + 1.5 * atr and price < walls["call_wall"] else 3

    def cat_pts(long):
        if not catalyst:
            return 0
        d = str(catalyst.get("direction", "")).lower()
        tier = int(catalyst.get("tier", 3)) if str(catalyst.get("tier", 3)).isdigit() else 3
        w = {1: 15, 2: 10, 3: 4}.get(tier, 4)
        return w if d == ("up" if long else "down") else -w if d == ("down" if long else "up") else 0

    def build(long):
        pts = {"trend": trend_pts(long), "rel_strength": rs_pts(long), "momentum": rsi_pts(long),
               "regime": regime_pts(long), "volume": vol_pts(), "oi": oi_pts(long), "catalyst": cat_pts(long)}
        return max(0, min(100, sum(pts.values()))), pts

    long_s, long_pts = build(True)
    short_s, short_pts = build(False)
    long = long_s >= short_s
    sc, pts = (long_s, long_pts) if long else (short_s, short_pts)
    side = "LONG" if long else "SHORT"

    # ---- framing: try 3 setup models the way a trader does, pick best R:R>=1.5 ----
    # momentum = buy/sell now; pullback = wait for the dip to support (bounce to
    # resistance for shorts); breakout = enter on the break of the level. Pullback and
    # breakout carry a TRIGGER price — a resting order — so extended days still produce
    # actionable plans instead of a flat sit-out.
    # sup is ascending, so the NEAREST support below price is sup[-1] (not sup[0]).
    s1 = sup[-1]["price"] if sup else price - 1.5 * atr
    s2 = sup[-2]["price"] if len(sup) > 1 else s1 - 1.5 * atr
    r1 = res[0]["price"] if res else price + 2 * atr   # res ascending: res[0] = nearest above
    r2 = res[1]["price"] if len(res) > 1 else r1 + 2 * atr
    s1lab = sup[-1]["label"] if sup else "support"
    r1lab = res[0]["label"] if res else "resistance"

    def _models(long):
        if long:
            return [
                ("momentum", price, None, round(max(s1 - 0.3 * atr, price - 2.5 * atr), 2), r1, r2, r1lab),
                ("pullback to " + s1lab, s1, s1, round(max(s2 - 0.3 * atr, s1 - 1.0 * atr), 2), r1, r2, r1lab)
                if s1 < price else None,
                ("breakout " + r1lab, round(r1 + 0.1 * atr, 2), r1, round(max(price - 0.5 * atr, r1 - 1.2 * atr), 2),
                 r2, round(r2 + 2 * atr, 2), "next res"),
            ]
        return [
            ("momentum", price, None, round(min(r1 + 0.3 * atr, price + 2.5 * atr), 2), s1, s2, s1lab),
            ("bounce to " + r1lab, r1, r1, round(min(s2 + 0.3 * atr, r1 + 1.0 * atr), 2), s1, s2, s1lab)
            if r1 > price else None,
            ("breakdown " + s1lab, round(s1 - 0.1 * atr, 2), s1, round(min(price + 0.5 * atr, s1 + 1.2 * atr), 2),
             s2, round(s2 - 2 * atr, 2), "next sup"),
        ]

    best = None
    for m in _models(long):
        if not m:
            continue
        typ, entry, trig, stp, t1, t2, lab = m
        risk = abs(entry - stp)
        if risk <= 0:
            continue
        rr = round(abs(t1 - entry) / risk, 2)
        if rr >= 1.5 and (best is None or rr > best["rr"]):
            best = {"setup": typ, "entry": round(entry, 2), "trigger": (round(trig, 2) if trig else None),
                    "stop": stp, "t1": round(t1, 2), "t2": round(t2, 2), "rr": rr, "t1_label": lab}

    thin = turnover_cr < 2.0
    action, levels = "WAIT", None
    if best:
        eta = max(1, round(abs(best["t1"] - best["entry"]) / atr)) if atr > 0 else None
        levels = {"entry": best["entry"], "trigger": best["trigger"], "stop": best["stop"],
                  "target1": best["t1"], "target2": best["t2"], "rr": best["rr"],
                  "t1_label": best["t1_label"], "eta_days": eta, "setup": best["setup"]}
    if thin:
        note = "too thin to trade (turnover < ₹2cr)"
    elif best and sc >= 60:
        action = "BUY" if long else "SELL"
        trg = f" · trigger ₹{best['trigger']}" if best["trigger"] else " · enter now"
        note = f"{side} {best['setup']}{trg} · score {sc} · R:R {best['rr']}"
    elif best and sc >= 50:
        note = f"watch: {best['setup']} R:R {best['rr']}, score {sc} (<60) — needs more confirmation"
    else:
        note = f"no clean setup (score {sc})"

    return {
        "symbol": sym, "action": action, "side": side, "score": sc, "confidence": sc,
        "price": round(price, 2),
        "levels": levels,
        "factors": pts,
        "evidence": {
            "trend": "UP" if price > ema20 > ema50 else "DOWN" if price < ema20 < ema50 else "MIXED",
            "rsi": round(rsi, 1), "atr": round(atr, 2), "rvol": round(rvol, 2),
            "rel_strength_vs_nifty_pct": rs, "nifty_regime": regime,
            "turnover_cr": round(turnover_cr, 1), "thin": thin,
            "nearest_support": nearest_sup, "nearest_resistance": nearest_res,
            "oi_walls": {"put": walls.get("put_wall"), "call": walls.get("call_wall"),
                         "pcr": walls.get("pcr")} if walls else None,
        },
        "note": note,
    }


if __name__ == "__main__":
    import sys, json
    for s in (sys.argv[1:] or ["RELIANCE"]):
        print(json.dumps(score(s), indent=2, default=str))
        print("=" * 60)
