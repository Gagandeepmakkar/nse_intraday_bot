"""Pre-market daily intelligence briefing for one (or a few) NSE stock(s).

Gathers, from FREE sources only, everything you'd want to read before the open:
  - Technical snapshot: last price, 5-day move, daily trend (EMA 20/50/200 stack)
  - Expected day range from ATR
  - Key intraday levels: central pivot range + Camarilla S/R, 20-day high/low
  - Momentum: RSI(14) with an overbought/oversold read
  - News headlines (Google News RSS) + a crude keyword sentiment tilt
  - Official corporate filings (NSE announcements), if reachable

HONEST SCOPE: this is a DECISION-SUPPORT briefing, not a prediction and not a
signal. News/sentiment edges are fleeting and usually already priced in; the
keyword sentiment here is a rough tilt, not a model. Use it to be informed and
to know your levels — not as a reason a trade will work. Nothing here guarantees
profit. Pair it with the VIM paper-trade expectancy before risking real money.

Usage:
    python briefing.py RELIANCE                 # one stock to stdout
    python briefing.py RELIANCE TCS HDFCBANK     # a few
    python briefing.py RELIANCE --email          # also email it (uses .env SMTP)
    python briefing.py RELIANCE --ntfy           # also push to your phone (NTFY_TOPIC)

Reads .env for SOURCE, SMTP_*, NTFY_TOPIC (via python-dotenv if present).
"""
import json
import os
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import indicators

# Crude sentiment lexicon — intentionally small and transparent. Not a model.
POS = {"surge", "jump", "rally", "gain", "gains", "profit", "beat", "beats", "upgrade",
       "record", "high", "strong", "growth", "wins", "win", "order", "bullish", "buy",
       "outperform", "raises", "approval", "approved", "expansion", "dividend", "bonus"}
NEG = {"fall", "falls", "drop", "slump", "plunge", "loss", "losses", "miss", "misses",
       "downgrade", "weak", "probe", "fraud", "fine", "penalty", "cut", "cuts", "lawsuit",
       "bearish", "sell", "underperform", "resign", "resigns", "default", "raid", "ban"}


def fetch_daily(symbol):
    """Daily OHLCV for the last ~3 months via yfinance. Returns a clean df or None."""
    try:
        import yfinance as yf
        raw = yf.download(symbol + ".NS", period="3mo", interval="1d",
                          progress=False, auto_adjust=False)
        if raw is None or raw.empty:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = raw.reset_index()[["Date", "Open", "High", "Low", "Close", "Volume"]]
        df.columns = ["date", "open", "high", "low", "close", "volume"]
        df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception as e:
        print(f"  (price fetch failed: {e})", file=sys.stderr)
        return None


def fetch_news(company, limit=6):
    """Recent headlines from Google News RSS (free, no key). Returns list of titles."""
    try:
        q = urllib.parse.quote(f"{company} NSE share")
        url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            root = ET.fromstring(r.read())
        titles = [it.findtext("title", "").strip() for it in root.iter("item")]
        return [t for t in titles if t][:limit]
    except Exception as e:
        print(f"  (news fetch failed: {e})", file=sys.stderr)
        return []


def fetch_filings(symbol):
    """Official NSE corporate announcements for the symbol today, if reachable."""
    try:
        from datasource import NSESource
        anns = NSESource().announcements(days=2)
        rows = anns.get(symbol) or anns.get(symbol.upper()) or []
        return [r.get("subject", "").strip() for r in rows][:4]
    except Exception:
        return []


def sentiment(headlines):
    """Crude pos/neg keyword tilt over headlines. Returns (label, score, p, n)."""
    words = " ".join(headlines).lower().replace(",", " ").replace(".", " ").split()
    p = sum(1 for w in words if w in POS)
    n = sum(1 for w in words if w in NEG)
    if p + n == 0:
        return "neutral (no strong words)", 0.0, p, n
    score = (p - n) / (p + n)
    label = "bullish tilt" if score > 0.2 else "bearish tilt" if score < -0.2 else "mixed/neutral"
    return label, score, p, n


def ai_news(symbol, headlines):
    """If GEMINI_API_KEY is set, let Gemini read the headlines and return a smarter
    (sentiment, caution) read. Returns None to fall back to keyword sentiment.
    HONEST: this sharpens news UNDERSTANDING and risk-flagging; it does not predict price."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key or not headlines:
        return None
    model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    hs = "\n- ".join(headlines)
    prompt = (
        f"You are a terse intraday-trading RISK assistant for the NSE stock {symbol}. "
        "From these headlines, reply ONLY as compact JSON: "
        '{"sentiment":"bullish|bearish|neutral","caution":"one short line naming any '
        "event that makes intraday trading risky today (earnings, probe, ban, big gap, "
        'none)"}. Do not predict price. Headlines:\n- ' + hs
    )
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={key}")
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
        txt = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        txt = txt.replace("```json", "").replace("```", "").strip()
        obj = json.loads(txt)
        return obj.get("sentiment", "neutral"), obj.get("caution", "none")
    except Exception as e:
        print(f"  (Gemini unavailable, using keyword sentiment: {e})", file=sys.stderr)
        return None


def analyse(symbol):
    df = fetch_daily(symbol)
    if df is None or len(df) < 30:
        return f"## {symbol}\n_No price data (source unreachable or symbol wrong)._\n"

    closes = df["close"]
    price = float(closes.iloc[-1])
    chg5 = (price / float(closes.iloc[-6]) - 1) * 100 if len(closes) > 6 else 0.0
    e20, e50, e200 = (indicators.ema(closes, 20).iloc[-1],
                      indicators.ema(closes, 50).iloc[-1],
                      indicators.ema(closes, 200).iloc[-1] if len(closes) >= 200 else float("nan"))
    if price > e20 > e50 and (pd.isna(e200) or e50 > e200):
        trend = "UP (price above a rising EMA stack)"
    elif price < e20 < e50 and (pd.isna(e200) or e50 < e200):
        trend = "DOWN (price below a falling EMA stack)"
    else:
        trend = "MIXED / rangebound (EMAs not aligned)"

    a = indicators.atr(df, 14)
    r = indicators.rsi(closes.tolist(), 14)
    rlabel = "overbought" if r >= 70 else "oversold" if r <= 30 else "neutral"
    hi20, lo20 = float(df["high"].tail(20).max()), float(df["low"].tail(20).min())

    last = df.iloc[-1]
    piv = indicators.central_pivot_range({"high": float(last["high"]),
                                          "low": float(last["low"]),
                                          "close": float(last["close"])})

    news = fetch_news(symbol)
    ai = ai_news(symbol, news)      # Gemini read if GEMINI_API_KEY is set, else None
    if ai:
        slabel, caution = ai[0] + " (AI)", ai[1]
    else:
        slabel, _s, sp, sn = sentiment(news)
        slabel, caution = f"{slabel} (+{sp}/−{sn})", None
    filings = fetch_filings(symbol)

    out = []
    out.append(f"## {symbol} — ₹{price:,.2f}  ({chg5:+.1f}% / 5d)")
    out.append(f"**Trend:** {trend}")
    out.append(f"**Expected day range:** ~₹{a:,.1f} (1 ATR) → roughly ₹{price-a:,.0f}–₹{price+a:,.0f}")
    out.append(f"**Momentum:** RSI {r:.0f} ({rlabel})")
    out.append("")
    out.append("**Key levels for today (from yesterday's range):**")
    out.append(f"- Pivot: ₹{piv['pivot']:,.1f}  ·  CPR ₹{piv['cpr_bottom']:,.1f}–₹{piv['cpr_top']:,.1f}")
    out.append(f"- Resistance: R3 ₹{piv['cam_h3']:,.1f} · R4 ₹{piv['cam_h4']:,.1f} · 20d-high ₹{hi20:,.1f}")
    out.append(f"- Support: S3 ₹{piv['cam_l3']:,.1f} · S4 ₹{piv['cam_l4']:,.1f} · 20d-low ₹{lo20:,.1f}")
    out.append("")
    out.append(f"**News sentiment:** {slabel}")
    if caution and str(caution).lower() not in ("none", "", "null"):
        out.append(f"⚠️ **Caution:** {caution}")
    if news:
        for t in news:
            out.append(f"- {t}")
    else:
        out.append("- (no headlines fetched)")
    if filings:
        out.append("")
        out.append("**Official NSE filings:**")
        for f in filings:
            out.append(f"- 📄 {f}")

    out.append("")
    return "\n".join(out)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    env_syms = [s.strip().upper() for s in os.getenv("BRIEF_SYMBOLS", "").split(",") if s.strip()]
    symbols = [s.upper() for s in args] or env_syms or ["RELIANCE"]

    header = f"# 📊 Pre-market briefing — {datetime.now():%d %b %Y, %H:%M}\n"
    body = header + "\n".join(analyse(s) for s in symbols)
    footer = ("\n---\n_Decision support only — not a prediction or signal. News/sentiment "
              "is a rough tilt, usually already priced in. Know your levels, size with VIM, "
              "and never trade a fixed daily target. Validate expectancy on paper first._")
    report = body + footer
    print(report)

    if "--email" in flags:
        try:
            import bot
            bot.notify_email("Pre-market briefing: " + ", ".join(symbols), report)
            print("\n[emailed]", file=sys.stderr)
        except Exception as e:
            print(f"\n[email failed: {e}]", file=sys.stderr)

    if "--ntfy" in flags:
        topic = os.getenv("NTFY_TOPIC", "").strip()
        if topic:
            try:
                summary = "\n".join(l for l in body.splitlines() if l.startswith("## ") or l.startswith("**Trend"))
                urllib.request.urlopen(urllib.request.Request(
                    "https://ntfy.sh/" + topic, data=summary.encode(),
                    headers={"Title": "Pre-market briefing", "Priority": "default"}), timeout=10)
                print("[pushed to phone]", file=sys.stderr)
            except Exception as e:
                print(f"[ntfy failed: {e}]", file=sys.stderr)


if __name__ == "__main__":
    main()
