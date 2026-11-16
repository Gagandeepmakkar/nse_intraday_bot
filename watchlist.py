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
import json
import os
import re
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


# Filings that actually move a stock vs routine noise (trading-window, AGM, newspaper ads).
MATERIAL_KW = re.compile(
    r"result|earning|order\b|contract|award|approv|acqui|merger|demerger|stake|fund.?rais|"
    r"qip|preferential|buy.?back|fraud|probe|investigat|resign|default|bonus|split|delist|"
    r"open offer|block deal|bulk deal|dividend|revenue|profit|guidance|downgrade|upgrade|"
    r"rating|allot|capex|expansion|capacity|launch|partnership|joint venture|supply|tender|"
    r"\bwon\b|\bbag|licen|patent|settlement|insolvency|nclt|penalt|clearance", re.I)


def ai_catalyst(symbol, text):
    """AI judges one filing as an event-driven analyst: materiality, an evidence-based IMPACT
    TIER (1 high / 2 medium / 3 low), the event type, and direction. Returns dict or None.
    Tiers come from research on which events actually move stocks (M&A, approvals, earnings
    surprises & order wins move most; analyst notes move ±1-3%; block deals lean down).
    Text-based classification (reading the filing) is the stronger form of PEAD. No magnitude."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key or not text:
        return None
    model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    prompt = (
        f"NSE stock {symbol} filed this with the exchange: \"{text[:900]}\". "
        "As an event-driven analyst, reply ONLY compact JSON: "
        '{"material": true|false, "tier": 1|2|3, "type": "<=4 words event type", '
        '"direction": "up"|"down"|"neutral", "reason": "<=10 words"}. '
        "TIER 1 (biggest moves): M&A / acquisition / merger / open offer, regulatory or drug "
        "approval, large order or contract win, earnings surprise (beat or miss), fundraise / "
        "QIP / preferential allotment, demerger, fraud / probe / insolvency. "
        "TIER 2 (medium): buyback, dividend / bonus / split, product launch, capacity expansion, "
        "management change, block / bulk deal, credit-rating change. "
        "TIER 3 (small, <=3%): analyst note, routine update, press clipping. "
        "Direction rules: earnings BEAT or big order = up; earnings MISS, block/bulk deal, "
        "promoter sell, fraud/probe = down. Routine filings (trading-window, AGM notice, "
        "newspaper publication, generic Reg-30 disclosure, analyst-call schedule) => material=false. "
        "Do NOT predict magnitude or guarantee.")
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            txt = json.load(r)["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(txt.replace("```json", "").replace("```", "").strip())
    except Exception:
        return None


def build(mode):
    src = NSESource()
    state = src.market_state()

    # CATALYST-FIRST: every stock with a filing today whose text looks material.
    cat = {}
    try:
        for sym, rows in (src.announcements(days=1) or {}).items():
            blob = " ".join((r.get("desc", "") + " " + r.get("text", "")) for r in rows)
            if MATERIAL_KW.search(blob):
                cat[sym] = (rows[0].get("desc") or "filing")[:60], blob
    except Exception:
        pass

    # Morning: pre-open gappers are a real same-day catalyst too (independent of filings).
    gaps = {}
    if mode == "morning":
        try:
            for g in src.preopen_gaps(1.5)[:40]:
                if g["last"] >= MIN_PRICE:
                    gaps[g["symbol"]] = g
        except Exception:
            pass

    # AI-classify each catalyst filing; keep only the ones AI calls material + directional.
    scored = []
    for sym, (desc, blob) in list(cat.items())[:30]:   # cap AI calls
        a = ai_catalyst(sym, blob)
        if not a or not a.get("material"):
            continue
        d = str(a.get("direction", "neutral")).lower()
        tier = int(a["tier"]) if str(a.get("tier", "3")).isdigit() else 3
        # rank: impact tier dominates (1>2>3), then a clear direction, then a confirming gap.
        rank = (4 - tier) * 10 + (5 if d in ("up", "down") else 0) + (4 if sym in gaps else 0)
        scored.append({"symbol": sym, "catalyst": desc, "ai_reason": a.get("reason", ""),
                       "type": a.get("type", ""), "tier": tier, "direction": d,
                       "gap": gaps.get(sym, {}).get("gap"), "imbalance": gaps.get(sym, {}).get("imbalance"),
                       "rank": rank})
    # Morning: fold in strong gappers that had no filing (the gap IS the signal).
    for sym, g in gaps.items():
        if sym in cat:
            continue
        scored.append({"symbol": sym, "catalyst": "pre-open gap", "ai_reason": "", "type": "gap",
                       "tier": 2, "direction": "up" if g["gap"] > 0 else "down", "gap": g["gap"],
                       "imbalance": g["imbalance"], "rank": 20 + min(abs(g["gap"]), 6)})

    scored.sort(key=lambda x: x["rank"], reverse=True)
    picks = []
    for s in scored[:6]:
        sym = s["symbol"]
        # final direction bias = AI catalyst lean + pre-open gap + imbalance
        b = {"up": 1, "down": -1}.get(s["direction"], 0)
        b += 2 if (s.get("gap") or 0) > 0 else -2 if (s.get("gap") or 0) < 0 else 0
        b += 1 if (s.get("imbalance") or 0) > 0.15 else -1 if (s.get("imbalance") or 0) < -0.15 else 0
        s["bias"] = "UP ▲" if b > 0 else "DOWN ▼" if b < 0 else "— unclear"
        s["tech"] = technicals(sym)
        picks.append(s)
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
            bits.append(f"pre-open {p['gap']:+.1f}%")
        if p.get("imbalance") is not None:
            bits.append("buy-heavy" if p["imbalance"] > 0.15 else "sell-heavy" if p["imbalance"] < -0.15 else "balanced")
        ctx = " (" + ", ".join(bits) + ")" if bits else ""
        tier = p.get("tier")
        tg = f"  [T{tier}{'·' + p['type'] if p.get('type') else ''}]" if tier else ""
        out.append(f"{i}. {p['symbol']}  bias {p['bias']}{ctx}{tg}")
        reason = (" — " + p["ai_reason"]) if p.get("ai_reason") else ""
        out.append(f"   catalyst: {p['catalyst']}{reason}")
        t = p.get("tech") or {}
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
                "https://ntfy.sh/" + os.getenv("NTFY_TOPIC").strip(), data=head.encode("utf-8"),
                headers={"Title": "Pre-market watchlist", "Tags": "dart", "Priority": "default"}), timeout=10)
            print("[pushed]", file=sys.stderr)
        except Exception as e:
            print(f"[ntfy failed: {e}]", file=sys.stderr)


def main():
    mode = "morning" if "--morning" in sys.argv else "evening"
    state, picks = build(mode)
    text = report(mode, state, picks)
    print(text)
    # Persist for the extension (catalyst fusion): symbol -> direction bias.
    try:
        simple = [{"symbol": p["symbol"],
                   "bias": "UP" if "UP" in p["bias"] else "DOWN" if "DOWN" in p["bias"] else "flat",
                   "catalyst": p.get("catalyst", "")} for p in picks]
        json.dump({"mode": mode, "picks": simple, "asof": datetime.now(TZ).isoformat()},
                  open("watchlist.json", "w", encoding="utf-8"))
    except Exception:
        pass
    push(text, picks)


if __name__ == "__main__":
    main()
