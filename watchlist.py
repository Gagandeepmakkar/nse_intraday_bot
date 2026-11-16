"""Pre-market WATCHLIST builder — before the open, surface the ~5 hottest stocks likely to
move today, with a direction BIAS and the reason, so you walk in with a plan instead of
guessing one stock.

Runs twice via the scheduler:
  --evening : after close. Scans the day's news/filing CATALYSTS + biggest movers (momentum
              carryover); AI reads each catalyst's lean. -> tomorrow's shortlist.
  --morning : before 09:15. Adds the NSE PRE-OPEN gaps + buy/sell imbalance (the strongest
              same-day signal) -> finalised list with direction bias + key levels.

HONEST SCOPE: this finds stocks LIKELY to move (catalyst + gap + volume) and leans a
direction from real signals (pre-open gap, imbalance, momentum, catalyst sentiment). It is
NOT a prediction — a gap can reverse in minutes. Trade the reaction with Signals/VIM, honour
the stop. No guarantees. AI only reads news text; every number here is deterministic.

Run: python watchlist.py --evening        (or --morning)
     python watchlist.py --morning --ntfy --email
"""
import os
import sys
import urllib.request
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import pandas as pd
import indicators
import briefing
from datasource import NSESource, TZ

MIN_PRICE = float(os.getenv("MOVERS_MIN_PRICE", "30"))


def technicals(symbol):
    """Trend + ATR day-range + key levels from daily candles. {} if unavailable."""
    df = briefing.fetch_daily(symbol)
    if df is None or len(df) < 30:
        return {}
    closes = df["close"]
    price = float(closes.iloc[-1])
    e20, e50 = indicators.ema(closes, 20).iloc[-1], indicators.ema(closes, 50).iloc[-1]
    trend = "UP" if price > e20 > e50 else "DOWN" if price < e20 < e50 else "MIXED"
    a = indicators.atr(df, 14)
    last = df.iloc[-1]
    piv = indicators.central_pivot_range({"high": float(last["high"]), "low": float(last["low"]), "close": float(last["close"])})
    return {"price": price, "trend": trend, "atr": a, "pivot": piv["pivot"],
            "res": piv["cam_h3"], "sup": piv["cam_l3"],
            "hi20": float(df["high"].tail(20).max()), "lo20": float(df["low"].tail(20).min())}


def build(mode):
    src = NSESource()
    state = src.market_state()
    cands = {}

    try:
        for sym, rows in (src.announcements(days=1) or {}).items():
            if rows:
                cands.setdefault(sym, {})["catalyst"] = (rows[0].get("desc") or "filing")[:48]
    except Exception:
        pass
    try:
        mv = src.top_movers()
        for key in ("gainers", "losers"):
            for r in mv.get(key, [])[:25]:
                if r["ltp"] < MIN_PRICE or abs(r["pChange"]) > 20:
                    continue
                cands.setdefault(r["symbol"], {}).update(pChange=r["pChange"], ltp=r["ltp"])
    except Exception:
        pass
    if mode == "morning":
        try:
            for g in src.preopen_gaps(1.0)[:40]:
                cands.setdefault(g["symbol"], {}).update(gap=g["gap"], imbalance=g["imbalance"])
        except Exception:
            pass

    # rank candidates by a move-likelihood score (gap weighs most, then catalyst, momentum)
    def score(f):
        return abs(f.get("gap", 0)) * 1.6 + (6 if f.get("catalyst") else 0) + abs(f.get("pChange", 0)) * 0.5
    ranked = sorted(cands.items(), key=lambda kv: score(kv[1]), reverse=True)[:10]

    picks = []
    for sym, f in ranked:
        heads = []
        try:
            heads = briefing.fetch_news(sym)
        except Exception:
            pass
        ai = None
        try:
            ai = briefing.ai_news(sym, heads)
        except Exception:
            pass
        sent = ai[0] if ai else (briefing.sentiment(heads)[0].split()[0] if heads else "neutral")
        caution = ai[1] if ai else ""
        # direction BIAS = sum of real directional signals (not a prediction)
        b = 0
        b += 2 if f.get("gap", 0) > 0 else -2 if f.get("gap", 0) < 0 else 0
        b += 1 if f.get("imbalance", 0) > 0.15 else -1 if f.get("imbalance", 0) < -0.15 else 0
        b += 1 if f.get("pChange", 0) > 0 else -1 if f.get("pChange", 0) < 0 else 0
        b += 1 if sent == "bullish" else -1 if sent == "bearish" else 0
        bias = "UP ▲" if b > 0 else "DOWN ▼" if b < 0 else "— flat"
        tech = technicals(sym)
        picks.append({"symbol": sym, "bias": bias, "catalyst": f.get("catalyst", ""),
                      "sent": sent, "caution": caution, "gap": f.get("gap"), "pChange": f.get("pChange"),
                      "imbalance": f.get("imbalance"), "tech": tech})
        if len(picks) >= 5:
            break
    return state, picks


def report(mode, state, picks):
    out = [f"# 🎯 Pre-market watchlist — {datetime.now(TZ):%d %b %H:%M} ({mode})", ""]
    if not picks:
        out.append("No candidates (market data unreachable, or nothing with a catalyst/gap).")
        return "\n".join(out)
    for i, p in enumerate(picks, 1):
        bits = []
        if p.get("gap") is not None:
            bits.append(f"gap {p['gap']:+.1f}%")
        if p.get("imbalance") is not None:
            bits.append("buy-heavy" if p["imbalance"] > 0.15 else "sell-heavy" if p["imbalance"] < -0.15 else "balanced")
        if p.get("pChange") is not None:
            bits.append(f"yest {p['pChange']:+.1f}%")
        ctx = " (" + ", ".join(bits) + ")" if bits else ""
        out.append(f"{i}. {p['symbol']}  bias {p['bias']}{ctx}")
        if p["catalyst"]:
            out.append(f"   catalyst: {p['catalyst']} · news {p['sent']}" + (f" ⚠ {p['caution']}" if p["caution"] and str(p["caution"]).lower() not in ("none", "") else ""))
        t = p["tech"]
        if t:
            out.append(f"   levels: pivot ₹{t['pivot']:,.1f} · sup ₹{t['sup']:,.1f} / res ₹{t['res']:,.1f} · day range ±₹{t['atr']:,.1f} · trend {t['trend']}")
        out.append("")
    out.append("_Stocks LIKELY to move with a direction bias from real signals — not a prediction. "
               "Open them in the extension, let Signals/VIM time the entry, honour the stop._")
    return "\n".join(out)


def push(text, picks):
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if "--email" in flags:
        try:
            import bot
            bot.notify_email("Pre-market watchlist: " + ", ".join(p["symbol"] for p in picks[:5]), text)
            print("[emailed]", file=sys.stderr)
        except Exception as e:
            print(f"[email failed: {e}]", file=sys.stderr)
    if "--ntfy" in flags and os.getenv("NTFY_TOPIC", "").strip():
        try:
            head = "\n".join(l for l in text.splitlines() if l and not l.startswith("_") and not l.startswith("#") and not l.startswith("   "))
            urllib.request.urlopen(urllib.request.Request(
                "https://ntfy.sh/" + os.getenv("NTFY_TOPIC").strip(), data=head.encode(),
                headers={"Title": "🎯 Pre-market watchlist", "Priority": "default"}), timeout=10)
            print("[pushed]", file=sys.stderr)
        except Exception as e:
            print(f"[ntfy failed: {e}]", file=sys.stderr)


def main():
    mode = "morning" if "--morning" in sys.argv else "evening"
    state, picks = build(mode)
    text = report(mode, state, picks)
    print(text)
    push(text, picks)


if __name__ == "__main__":
    main()
