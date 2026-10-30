"""Notification-only intraday research engine for NSE cash equities.

Deliberately contains NO order-placement calls. It discovers and ranks up to
MAX_CANDIDATES intraday setups across the liquid NSE universe, computes net
reward:risk after Indian transaction costs, and labels each setup CONFIRMED /
PENDING / UNVERIFIED. Personal research use only; not investment advice and not
for redistribution (distributing stock-specific calls may require SEBI Research
Analyst registration). Review every signal manually before acting.

Run live:    python bot.py            (needs KITE_API_KEY + token.json)
Run offline: python bot.py --offline  (deterministic synthetic data; no creds)
"""
import os
import re
import sys
import json
import time
import html
import sqlite3
import smtplib
import logging
import webbrowser
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote as urlquote
from datetime import datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv

import indicators as ind
import costs
from datasource import get_source

load_dotenv()
TZ = ZoneInfo(os.getenv("MARKET_TIMEZONE", "Asia/Kolkata"))
DB_PATH = Path("signals.db")
DASHBOARD_PATH = Path("dashboard.html")


def _load_sectors():
    p = Path("sectors.json")
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        return {}


SECTORS = _load_sectors()  # symbol -> industry/sector (from Nifty 500 list)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Composite score weights (sum to 100). News-led: catalyst is the heaviest single
# factor, so a stock with a real catalyst (earnings/order/upgrade/deal) outranks a
# no-news stock of similar technicals. Components whose data is unavailable receive
# a neutral 50 so they neither reward nor penalize — see score_candidate().
WEIGHTS = {
    "relative_strength": 15,
    "rvol": 15,
    "price_structure": 14,
    "liquidity": 12,
    "risk_feasibility": 14,
    "order_flow": 5,
    "catalyst": 25,
}


def env_float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return int(default)


def _arg_value(flag, default):
    """Read `--flag value` from argv, else return default."""
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


# --------------------------------------------------------------------------- #
# Notifications and news (unchanged behavior from the original scanner)
# --------------------------------------------------------------------------- #
def notify_telegram(text):
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logging.warning("Telegram not configured; skipping.")
        return
    for i in range(0, len(text), 3800):  # Telegram 4096-char limit; split safely.
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": text[i:i + 3800]}, timeout=15)
        r.raise_for_status()


def notify_email(subject, text):
    host = os.getenv("SMTP_HOST")
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("EMAIL_FROM") or username
    recipient = os.getenv("EMAIL_TO")
    if not all([host, username, password, sender, recipient]):
        logging.warning("Email not configured; skipping.")
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sender, recipient
    msg.set_content(text)
    with smtplib.SMTP(host, env_int("SMTP_PORT", 587), timeout=20) as smtp:
        smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(msg)


def load_news(symbols):
    p = Path(os.getenv("NEWS_CSV", "news.csv"))
    if not p.exists():
        return {}
    out = {}
    try:
        df = pd.read_csv(p, comment="#")
        if not {"symbol", "headline"}.issubset(df.columns):
            return {}
        for _, row in df.iterrows():
            sym = str(row.get("symbol", "")).strip().upper()
            headline = str(row.get("headline", "")).strip()
            if sym in symbols and headline and headline.lower() != "nan":
                out.setdefault(sym, []).append(headline)
    except Exception as exc:
        logging.warning("Could not read news CSV: %s", exc)
    return out


def ai_news_note(symbol, headlines):
    if not headlines:
        return "No configured company-specific headlines available."
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return "Headlines found; AI summary disabled (OPENAI_API_KEY not configured)."
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        prompt = ("Summarize these headlines for an intraday equity research note in 2 short bullets. "
                  "Do not infer unreported facts, predict returns, or call a trade by itself. "
                  "If stale/unclear, say so. Headlines:\n" + "\n".join("- " + h for h in headlines[:8]))
        resp = client.responses.create(model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"), input=prompt)
        return resp.output_text.strip()[:900]
    except Exception as exc:
        logging.warning("AI news summary failed for %s: %s", symbol, exc)
        return "AI summary unavailable; verify source headlines manually."


def fetch_live_news(symbol, max_items=4):
    """Latest ~24h headlines for a symbol via Google News RSS (free, no key).

    Returns a list of "headline — source" strings, newest first; empty on any
    error. News is a reading aid for your own judgment, never a trade trigger —
    verify each headline at its source before acting on it.
    """
    q = urlquote(f"{symbol} share price when:1d")
    url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        r.raise_for_status()
        items = ET.fromstring(r.content).findall(".//item")
    except Exception as exc:
        logging.warning("Live news fetch failed for %s: %s", symbol, exc)
        return []
    out = []
    for it in items:
        title = (it.findtext("title") or "").strip()
        if not title or "price, news, quote and history" in title.lower():
            continue  # skip Yahoo's auto price-page entry
        out.append(title)
        if len(out) >= max_items:
            break
    return out


_BULLISH_RE = re.compile(
    r"\b(rises?|rose|jumps?|surges?|soars?|gains?|beats?|record|wins?|bags|"
    r"acquir\w*|upgrade|buyback|bonus|dividend|approv\w*|rally|rallies|"
    r"expand\w*|strong|growth|boost\w*)\b", re.IGNORECASE)
_BEARISH_RE = re.compile(
    r"\b(falls?|fell|drops?|declines?|slumps?|plunges?|misses?|loss|losses|"
    r"cuts?|downgrade|probe|fraud|resigns?|penal\w*|recall|warning|weak|"
    r"concerns?|lawsuit|raid|slump)\b", re.IGNORECASE)


def news_direction(headlines):
    """Rough bullish/bearish lean of the headlines so the trade side can agree with
    the news (e.g. don't go LONG into a profit miss). NEUTRAL when it's a wash."""
    text = " ".join(headlines)
    bull, bear = len(_BULLISH_RE.findall(text)), len(_BEARISH_RE.findall(text))
    if bull > bear:
        return "BULLISH"
    if bear > bull:
        return "BEARISH"
    return "NEUTRAL"


# NSE filing categories/text that can actually move a stock intraday — as opposed to
# routine filings (general updates, trading-window closure, newspaper publication,
# share-transfer windows) that dominate the feed and should NOT count as a catalyst.
_MATERIAL_RE = re.compile(
    r"\b(result|earnings|profit|revenue|ebitda|order|contract|award|bags?|wins?|"
    r"acqui\w*|merger|amalgamat\w*|arrangement|stake|buy\s*-?\s*back|bonus|dividend|"
    r"split|rating|upgrade|downgrade|fund\s*rais\w*|preferential|qip|allotment|"
    r"open\s+offer|delist\w*|material\s+(issue|event)|investor\s+presentation|"
    r"outcome\s+of\s+board)\b", re.IGNORECASE)


def classify_announcements(anns):
    """From a symbol's NSE filings return (has_material, direction, texts).

    Materiality comes from the authoritative, symbol-tagged filing — not a fuzzy web
    search — so it can't be a false positive from another company's headline. Direction
    is a weak lean from the filing wording (often NEUTRAL, since filings are dry)."""
    texts = [f"{a['desc']}: {a['text']}".strip(" :") for a in anns if a.get("desc") or a.get("text")]
    blob = " ".join(texts)
    material = bool(_MATERIAL_RE.search(blob))
    return material, news_direction([blob]), texts[:4]


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS signals_v2 (
            created_at TEXT, symbol TEXT, side TEXT, label TEXT, price REAL,
            entry REAL, stop REAL, target1 REAL, target2 REAL, qty INTEGER,
            gross_rr REAL, net_rr REAL, rel_volume REAL, score REAL,
            regime TEXT, reason TEXT
        )""")


# --------------------------------------------------------------------------- #
# Universe, breadth and regime (cheap bulk-quote layer — covers ALL liquid names)
# --------------------------------------------------------------------------- #
def build_universe(src):
    """Return {symbol: instrument_row} for NSE cash EQ instruments."""
    rows = src.instruments("NSE")
    return {r["tradingsymbol"]: r for r in rows
            if r.get("segment") == "NSE" and r.get("instrument_type") == "EQ"}


def bulk_market_snapshot(src, symbols):
    """Quote every symbol in bulk for turnover, day-change and breadth.

    Returns (snapshot, breadth) where snapshot[sym] has last_price, prev_close,
    day_change_pct, turnover (today traded value), buy_qty, sell_qty, spread_pct.
    """
    keys = [f"NSE:{s}" for s in symbols]
    quotes = src.quote(keys)
    snap, adv, dec = {}, 0, 0
    for sym in symbols:
        q = quotes.get(f"NSE:{sym}")
        if not q:
            continue
        last = float(q.get("last_price") or 0)
        prev_close = float((q.get("ohlc") or {}).get("close") or 0)
        vol = float(q.get("volume") or 0)
        if last <= 0 or prev_close <= 0:
            continue
        change = (last - prev_close) / prev_close * 100
        if last > prev_close:
            adv += 1
        elif last < prev_close:
            dec += 1
        depth = q.get("depth") or {}
        bids, asks = depth.get("buy") or [], depth.get("sell") or []
        spread_pct = 0.0
        if bids and asks and bids[0].get("price") and asks[0].get("price"):
            mid = (bids[0]["price"] + asks[0]["price"]) / 2
            if mid > 0:
                spread_pct = (asks[0]["price"] - bids[0]["price"]) / mid * 100
        snap[sym] = {
            "last_price": last, "prev_close": prev_close, "day_change_pct": change,
            "turnover": last * vol, "buy_qty": float(q.get("buy_quantity") or 0),
            "sell_qty": float(q.get("sell_quantity") or 0), "spread_pct": spread_pct,
        }
    breadth = {"advancers": adv, "decliners": dec,
               "ratio": (adv / dec if dec else float(adv))}
    return snap, breadth


def classify_regime(src, breadth):
    """Hypothesis classification from Nifty50/BankNifty day position and breadth."""
    idx = src.quote(["NSE:NIFTY 50", "NSE:NIFTY BANK"])
    nifty_change = 0.0
    details = []
    for key, name in [("NSE:NIFTY 50", "Nifty50"), ("NSE:NIFTY BANK", "BankNifty")]:
        q = idx.get(key)
        if not q:
            continue
        last = float(q.get("last_price") or 0)
        prev = float((q.get("ohlc") or {}).get("close") or 0)
        if last and prev:
            ch = (last - prev) / prev * 100
            details.append(f"{name} {ch:+.2f}%")
            if name == "Nifty50":
                nifty_change = ch
    ratio = breadth["ratio"]
    if nifty_change > 0.15 and ratio > 1.3:
        regime = "BULLISH"
    elif nifty_change < -0.15 and ratio < 0.77:
        regime = "BEARISH"
    elif abs(nifty_change) <= 0.15:
        regime = "RANGEBOUND"
    else:
        regime = "MIXED"
    return regime, nifty_change, "; ".join(details) or "indices unavailable"


# --------------------------------------------------------------------------- #
# Per-symbol deep scan and scoring
# --------------------------------------------------------------------------- #
def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def score_candidate(sym, snap_row, df, nifty_change, regime, offline, news):
    """Run the layered screen on one symbol's candles. Returns a candidate dict
    or None if a hard gate fails (reason logged)."""
    if df.empty or "date" not in df.columns:
        return None  # no usable candles (e.g. REITs with thin/odd intraday data)
    df = ind.prepare(df)
    if len(df) < 30:
        return None
    # Drop the still-forming candle; indicators use completed candles only.
    if len(df) > 1:
        df = df.iloc[:-1].copy()
    if len(df) < 30:
        return None

    price = snap_row["last_price"]
    # Skip circuit-locked / non-trading names: when the latest candles are pinned
    # (near-zero range) the stock is frozen at a limit and can't be entered or
    # exited at a real price — e.g. a stock stuck at upper circuit.
    recent = df.tail(3)
    if float(((recent["high"] - recent["low"]) / recent["close"]).max()) < 0.001:
        return None
    rvol, rvol_sessions = ind.time_adjusted_rvol(df, env_int("RVOL_MIN_SESSIONS", 10))
    if rvol < env_float("MIN_RELATIVE_VOLUME", 1.5):
        return None

    df["ema20"], df["ema50"] = ind.ema(df["close"], 20), ind.ema(df["close"], 50)
    ema20, ema50 = float(df["ema20"].iloc[-1]), float(df["ema50"].iloc[-1])
    vwap = ind.session_vwap(df)
    atr5 = ind.atr(df)
    orb = ind.opening_range(df)
    hlc = ind.prev_day_hlc(df)
    cpr = ind.central_pivot_range(hlc) if hlc else None
    last_close = float(df["close"].iloc[-1])

    # Same-day tradeability: a stock needs real intraday range to clear a 2R target
    # after costs. Average the prior sessions' high-low range as % of close; skip
    # stocks too quiet to be worth trading intraday.
    day_ranges = [(float(g["high"].max()) - float(g["low"].min())) / float(g["close"].iloc[-1]) * 100
                  for _, g in df.groupby(df["date"].dt.date)]
    prior_ranges = day_ranges[:-1] or day_ranges
    avg_range_pct = sum(prior_ranges) / len(prior_ranges) if prior_ranges else 0.0
    if avg_range_pct < env_float("MIN_DAILY_RANGE_PCT", 1.2):
        return None
    # Opening gap vs prior close — a core intraday signal.
    today_rows = df[df["date"].dt.date == df["date"].iloc[-1].date()]
    base_close = hlc["close"] if hlc else float(today_rows["open"].iloc[0])
    gap_pct = (float(today_rows["open"].iloc[0]) - base_close) / base_close * 100 if base_close else 0.0

    # Direction from trend alignment; skip when structure is mixed.
    if price > vwap and ema20 > ema50:
        side = "LONG"
    elif price < vwap and ema20 < ema50:
        side = "SHORT"
    else:
        return None

    if side == "LONG":
        trigger = max(orb["high"] if orb else 0, float(df.tail(6)["high"].max()))
        stop = min(float(df.tail(6)["low"].min()), trigger - atr5)
        if stop >= trigger:
            return None
        entry = trigger
        risk = entry - stop
        # Realistic targets: bounded by a fraction of the stock's avg daily range
        # (entry is mid-session), snapped to the nearest real resistance price would
        # stall at (prior-day high, Camarilla H3/H4, CPR top).
        rng = entry * avg_range_pct / 100.0
        cap1 = entry + env_float("T1_RANGE_FRAC", 0.4) * rng
        cap2 = entry + env_float("T2_RANGE_FRAC", 0.7) * rng
        res = sorted(l for l in [hlc["high"] if hlc else None,
                                 cpr["cam_h3"] if cpr else None, cpr["cam_h4"] if cpr else None,
                                 cpr["cpr_top"] if cpr else None] if l and l > entry)
        t1 = next((l for l in res if l <= cap1), cap1)
        t2 = next((l for l in res if l > t1 and l <= cap2), cap2)
        confirmed_trigger = last_close > trigger and price >= trigger
    else:
        trigger = min(orb["low"] if orb else 1e18, float(df.tail(6)["low"].min()))
        stop = max(float(df.tail(6)["high"].max()), trigger + atr5)
        if stop <= trigger:
            return None
        entry = trigger
        risk = stop - entry
        # Realistic targets (mirror of LONG): bounded by range, snapped to the
        # nearest real support (prior-day low, Camarilla L3/L4, CPR bottom).
        rng = entry * avg_range_pct / 100.0
        cap1 = entry - env_float("T1_RANGE_FRAC", 0.4) * rng
        cap2 = entry - env_float("T2_RANGE_FRAC", 0.7) * rng
        sup = sorted((l for l in [hlc["low"] if hlc else None,
                                  cpr["cam_l3"] if cpr else None, cpr["cam_l4"] if cpr else None,
                                  cpr["cpr_bottom"] if cpr else None] if l and l < entry), reverse=True)
        t1 = next((l for l in sup if l >= cap1), cap1)
        t2 = next((l for l in sup if l < t1 and l >= cap2), cap2)
        confirmed_trigger = last_close < trigger and price <= trigger

    if risk <= 0:
        return None

    # Position sizing: capped by both risk budget and available cash.
    capital = env_float("TRADING_CAPITAL_INR", 100000)
    risk_budget = capital * env_float("RISK_PER_TRADE_PCT", 0.5) / 100
    qty = max(0, min(int(risk_budget / risk), int(capital / max(entry, 0.01))))
    if qty < 1:
        return None

    gross_rr, net_rr, _, _ = costs.net_reward_risk(side, entry, stop, t1, qty)
    if net_rr < env_float("MIN_NET_REWARD_RISK", 1.3):
        return None

    # ---- Component scores (0-100). Missing data -> neutral 50 (disclosed). ----
    rs = snap_row["day_change_pct"] - nifty_change
    rs_dir = rs if side == "LONG" else -rs
    s_rs = _clamp(50 + rs_dir * 12)
    s_rvol = _clamp((rvol - 0.5) / 2.5 * 100)
    aligned = [price > vwap if side == "LONG" else price < vwap,
               ema20 > ema50 if side == "LONG" else ema20 < ema50,
               confirmed_trigger,
               (price > cpr["pivot"]) if (cpr and side == "LONG")
               else ((price < cpr["pivot"]) if cpr else False)]
    s_struct = sum(1 for a in aligned if a) / len(aligned) * 100
    threshold = env_float("MIN_AVG_DAILY_TURNOVER_INR", 50000000)
    s_liq = _clamp(snap_row["turnover"] / (5 * threshold) * 100)
    if snap_row["spread_pct"] > 0:
        s_liq = _clamp(s_liq - snap_row["spread_pct"] * 20)
    bq, sq = snap_row["buy_qty"], snap_row["sell_qty"]
    imb = (bq - sq) / (bq + sq) if (bq + sq) > 0 else 0.0
    imb_dir = imb if side == "LONG" else -imb
    s_flow = _clamp(50 + imb_dir * 100)
    s_risk = _clamp((net_rr - 1) / 2 * 100)
    headlines = news.get(sym, [])
    s_catalyst = 60.0 if headlines else 50.0  # neutral when no verified catalyst

    comps = {"relative_strength": s_rs, "rvol": s_rvol, "price_structure": s_struct,
             "liquidity": s_liq, "risk_feasibility": s_risk, "order_flow": s_flow,
             "catalyst": s_catalyst}
    score = sum(comps[k] * WEIGHTS[k] for k in WEIGHTS) / sum(WEIGHTS.values())

    # Freshness / label (live only; mock data uses fixed historical dates).
    label = "CONFIRMED" if confirmed_trigger else "PENDING"
    if not offline:
        age_min = (datetime.now(TZ).replace(tzinfo=None) - df["date"].iloc[-1]).total_seconds() / 60
        if age_min > env_int("STALE_CANDLE_MINUTES", 20):
            label = "UNVERIFIED"

    reason = (f"{side} {regime}; price {price:.2f} vs VWAP {vwap:.2f}, "
              f"EMA20 {ema20:.2f}/EMA50 {ema50:.2f}; RVOL {rvol:.2f}x "
              f"({'baseline ' + str(rvol_sessions) + ' sessions' if rvol_sessions else 'recent-candle fallback'}); "
              f"RS vs Nifty {rs:+.2f}%; net R:R {net_rr:.2f}.")

    return {"symbol": sym, "side": side, "label": label, "price": price,
            "entry": entry, "stop": stop, "target1": t1, "target2": t2, "qty": qty,
            "gross_rr": gross_rr, "net_rr": net_rr, "rel_volume": rvol,
            "gap_pct": gap_pct, "avg_range_pct": avg_range_pct,
            "day_change_pct": snap_row["day_change_pct"],
            "score": score, "components": comps, "reason": reason}


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def market_status(now):
    t = now.time()
    wd = now.weekday()
    if wd >= 5:
        return "CLOSED (weekend)"
    if t < datetime.strptime("09:15", "%H:%M").time():
        return "PRE-OPEN"
    if t <= datetime.strptime("15:30", "%H:%M").time():
        return "OPEN"
    return "CLOSED"


def _gainer_lines(snap):
    """Return (count, lines) for every liquid name up on the day, by % change."""
    gainers = sorted(((s, r) for s, r in snap.items() if r["day_change_pct"] > 0),
                     key=lambda x: x[1]["day_change_pct"], reverse=True)
    out = [f"{'#':>3}  {'SYMBOL':<13}{'CHG%':>8}{'LTP':>11}{'TURNOVER(Cr)':>14}", "-" * 49]
    for i, (s, r) in enumerate(gainers, 1):
        out.append(f"{i:>3}  {s:<13}{r['day_change_pct']:>+7.2f}%{r['last_price']:>11.2f}"
                   f"{r['turnover'] / 1e7:>13.1f}")
    if not gainers:
        out.append("None — no liquid name is up on the day.")
    return len(gainers), out


def print_gainers(snap):
    """Print every liquid NSE name that is up on the day, sorted by % change."""
    n, lines = _gainer_lines(snap)
    print(f"\nGAINERS — {n} of {len(snap)} liquid NSE names up "
          f"({datetime.now(TZ).strftime('%Y-%m-%d %H:%M IST')}, data ~15m delayed)\n")
    print("\n".join(lines))


def _diversify(picks, n, max_per_sector=1):
    """Pick the top n by score while limiting correlated names: at most
    max_per_sector from any one sector (avoids two near-identical bets)."""
    out, seen = [], {}
    for r in picks:  # assumes already sorted by score desc
        sec = r.get("sector") or r["symbol"]
        if seen.get(sec, 0) >= max_per_sector:
            continue
        out.append(r)
        seen[sec] = seen.get(sec, 0) + 1
        if len(out) >= n:
            break
    return out


def _header_lines(stamp, status, regime, regime_detail, breadth, coverage):
    return [
        f"NSE INTRADAY ENGINE — {stamp}",
        f"Market: {status} | Regime: {regime} ({regime_detail})",
        f"Breadth A/D: {breadth['advancers']}/{breadth['decliners']} (ratio {breadth['ratio']:.2f})",
        f"Coverage: {coverage['available']} NSE EQ available, {coverage['liquid']} "
        f"passed liquidity, {coverage['scanned']} deep-scanned, {coverage['qualified']} qualified.",
        "Personal research only — NOT advice, NOT redistribution. Manual execution; "
        "verify price/liquidity/trigger before acting.",
        "",
    ]


def _pick_lines(n, r):
    move1 = abs(r["target1"] - r["entry"]) / r["entry"] * 100
    move2 = abs(r["target2"] - r["entry"]) / r["entry"] * 100
    lines = [
        f"{n}. {r['symbol']} (NSE) — {r['side']} [{r['label']}] | score {r['score']:.1f}/100",
        f"   Quote ₹{r['price']:.2f} | Entry/trigger ₹{r['entry']:.2f} | Stop ₹{r['stop']:.2f}",
        f"   T1 ₹{r['target1']:.2f} ({move1:.2f}% move) | T2 ₹{r['target2']:.2f} ({move2:.2f}% move) "
        f"| gross R:R {r['gross_rr']:.2f} | net R:R {r['net_rr']:.2f}",
        f"   Qty {r['qty']} | RVOL {r['rel_volume']:.2f}x | "
        f"Day {r.get('day_change_pct', 0):+.2f}% | Gap {r.get('gap_pct', 0):+.2f}% | "
        f"Avg day-range {r.get('avg_range_pct', 0):.2f}% | Daily trend {r.get('daily_trend', 'FLAT')}",
        f"   {r['reason']}",
    ]
    if r.get("sector"):
        lines.append(f"   Sector: {r['sector']} ({r.get('sector_chg', 0):+.2f}% avg today)")
    headlines = r.get("news_headlines") or []
    src_lbl = r.get("catalyst_src", "web news")
    if headlines:
        tag = "CATALYST FOUND" if r.get("catalyst") else "no verified catalyst"
        lines.append(f"   {src_lbl} ({tag}, {r.get('news_direction', 'NEUTRAL')}; "
                     f"verify before acting):")
        lines += [f"     - {h}" for h in headlines]
    else:
        lines.append("   No NSE filing or news found today (verify manually).")
    lines.append("")
    return lines


_FOOT = ("Labels: CONFIRMED=triggered on a completed 5m candle; PENDING=await trigger; "
         "UNVERIFIED=stale/after-hours data.\n"
         "EXIT PLAN (backtested): book ~half at T1, move the stop to breakeven, let the rest "
         "run to T2. Only trade WITH the daily trend.\n"
         "Rule-based candidates with DEFINED RISK — not predictions or guarantees. Honor the "
         "stop; entry levels go stale — do not chase.")


def build_report(stamp, status, regime, regime_detail, breadth, coverage, results):
    lines = _header_lines(stamp, status, regime, regime_detail, breadth, coverage)
    if not results:
        return "\n".join(lines + [
            "NO QUALIFIED TRADE — no symbol cleared the RVOL + trend + net-R:R + range gates."])
    for n, r in enumerate(results, 1):
        lines += _pick_lines(n, r)
    lines.append(_FOOT)
    return "\n".join(lines)


def build_combined(stamp, status, regime, regime_detail, breadth, coverage, snap, buy_list, top_list):
    """One report with all sections from a single scan: gainers, investable gainers
    (long setups with entry/SL/target), and the overall top setups."""
    lines = _header_lines(stamp, status, regime, regime_detail, breadth, coverage)
    ng, gl = _gainer_lines(snap)
    lines += [f"═══ GAINERS — {ng} up today ═══", ""] + gl + [""]
    lines += [f"═══ INVESTABLE GAINERS — {len(buy_list)} long setup(s) with entry/SL/target ═══", ""]
    for n, r in enumerate(buy_list, 1):
        lines += _pick_lines(n, r)
    if not buy_list:
        lines += ["  (no gainer has a clean long setup right now)", ""]
    lines += [f"═══ ALL TOP SETUPS — {len(top_list)} best (long & short) ═══", ""]
    for n, r in enumerate(top_list, 1):
        lines += _pick_lines(n, r)
    lines.append(_FOOT)
    return "\n".join(lines)


_DASH_CSS = """
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{margin:0;font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;background:#0f1419;color:#e6e9ee}
.wrap{max-width:900px;margin:0 auto;padding:24px 16px}
h1{font-size:20px;margin:0 0 4px}
.sub{color:#9aa4b2;font-size:13px;margin-bottom:16px}
.meta{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px}
.pill{background:#1b232e;border:1px solid #2a3542;border-radius:999px;padding:4px 12px;font-size:13px}
.card{background:#161b22;border:1px solid #273040;border-left-width:5px;border-radius:12px;padding:16px 18px;margin-bottom:16px}
.card.LONG{border-left-color:#2ecc71}.card.SHORT{border-left-color:#e74c3c}
.chead{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.rank{font-size:18px;font-weight:700}.sym{font-size:18px;font-weight:700}
.score{margin-left:auto;font-size:13px;color:#9aa4b2}
.badge{font-size:11px;font-weight:700;padding:3px 9px;border-radius:6px;letter-spacing:.3px}
.b-LONG{background:#123524;color:#4ade80}.b-SHORT{background:#3a1515;color:#f87171}
.b-CONFIRMED{background:#123524;color:#4ade80}.b-PENDING{background:#3a2e12;color:#fbbf24}.b-UNVERIFIED{background:#2a2f38;color:#9aa4b2}
.b-cat{background:#102a3a;color:#38bdf8}.b-up{background:#123524;color:#4ade80}.b-down{background:#3a1515;color:#f87171}.b-flat{background:#2a2f38;color:#9aa4b2}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:10px 0}
.kv{background:#0f1419;border:1px solid #232c38;border-radius:8px;padding:8px 10px}
.kv .k{font-size:11px;color:#8a94a2;text-transform:uppercase;letter-spacing:.4px}
.kv .v{font-size:16px;font-weight:600}
.reason{font-size:12.5px;color:#9aa4b2;margin:8px 0}
.news{margin:8px 0 0}.news .nt{font-size:12px;color:#8a94a2;margin-bottom:4px}
.news ul{margin:0;padding-left:18px}.news li{font-size:13px;margin:2px 0}
.chart{height:220px;margin:12px 0 4px;border:1px solid #232c38;border-radius:8px;overflow:hidden}
.foot{color:#7a8494;font-size:12px;margin-top:20px;border-top:1px solid #232c38;padding-top:12px}
"""

# TradingView's lightweight-charts (free, MIT) draws each pick as a candlestick with
# the entry/stop/T1/T2 drawn on it. Loaded from a CDN, so the dashboard needs
# internet (the bot already does); if it can't load, the cards still render and the
# chart boxes stay empty.
# Pinned to v4: the unversioned URL now serves v5, which removed the
# addCandlestickSeries()/createPriceLine() API this code uses (charts would go blank).
_DASH_CDN = "https://unpkg.com/lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js"
_DASH_JS = """
function mkChart(id, d){
  var el=document.getElementById(id);
  if(!el||!window.LightweightCharts||!d.candles||!d.candles.length)return;
  var chart=LightweightCharts.createChart(el,{height:220,width:el.clientWidth,
    layout:{background:{color:'transparent'},textColor:'#9aa4b2'},
    grid:{vertLines:{color:'#1b232e'},horzLines:{color:'#1b232e'}},
    timeScale:{timeVisible:true,secondsVisible:false,borderColor:'#273040'},
    rightPriceScale:{borderColor:'#273040'}});
  var s=chart.addCandlestickSeries({upColor:'#2ecc71',downColor:'#e74c3c',
    borderVisible:false,wickUpColor:'#2ecc71',wickDownColor:'#e74c3c'});
  s.setData(d.candles);
  function line(p,c,t){if(p)s.createPriceLine({price:p,color:c,lineWidth:1,
    lineStyle:2,axisLabelVisible:true,title:t});}
  line(d.entry,'#38bdf8','Entry');line(d.stop,'#f87171','Stop');
  line(d.t1,'#fbbf24','T1');line(d.t2,'#4ade80','T2');
  chart.timeScale().fitContent();
  new ResizeObserver(function(){chart.applyOptions({width:el.clientWidth});}).observe(el);
}
Object.keys(CHARTS).forEach(function(id){mkChart(id,CHARTS[id]);});
"""


def _candle_series(candles, limit=120):
    """Convert raw candle dicts to lightweight-charts points. The IST wall-clock is
    fed as a UTC epoch so the chart's time axis reads IST (lightweight-charts labels
    the axis in UTC, with no built-in timezone)."""
    out = []
    for c in candles[-limit:]:
        d = pd.Timestamp(c["date"])
        if d.tzinfo is not None:
            d = d.tz_convert(TZ).tz_localize(None)
        out.append({"time": int(d.tz_localize("UTC").timestamp()),
                    "open": round(float(c["open"]), 2), "high": round(float(c["high"]), 2),
                    "low": round(float(c["low"]), 2), "close": round(float(c["close"]), 2)})
    return out


def _badge(text, cls):
    return f'<span class="badge {cls}">{html.escape(text)}</span>'


def build_dashboard_html(stamp, status, regime, regime_detail, breadth, coverage, results):
    cards, charts = [], {}
    for n, r in enumerate(results, 1):
        side, label = r["side"], r["label"]
        trend = r.get("daily_trend", "FLAT")
        kvs = [
            ("Quote", f"₹{r['price']:.2f}"), ("Entry", f"₹{r['entry']:.2f}"),
            ("Stop", f"₹{r['stop']:.2f}"), ("Target 1", f"₹{r['target1']:.2f}"),
            ("Target 2", f"₹{r['target2']:.2f}"), ("Qty", str(r["qty"])),
            ("Net R:R", f"{r['net_rr']:.2f}"), ("RVOL", f"{r['rel_volume']:.2f}x"),
            ("Gap", f"{r.get('gap_pct', 0):+.2f}%"),
            ("Day range", f"{r.get('avg_range_pct', 0):.2f}%"),
        ]
        grid = "".join(f'<div class="kv"><div class="k">{html.escape(k)}</div>'
                       f'<div class="v">{html.escape(v)}</div></div>' for k, v in kvs)
        badges = (_badge(side, f"b-{side}") + _badge(label, f"b-{label}")
                  + _badge(f"Trend {trend}", f"b-{trend.lower()}"))
        if r.get("catalyst"):
            badges += _badge(f"CATALYST {r.get('news_direction', 'NEUTRAL')}", "b-cat")
        headlines = r.get("news_headlines") or []
        if headlines:
            src_lbl = r.get("catalyst_src", "web news")
            news = (f'<div class="news"><div class="nt">{html.escape(src_lbl)} — verify before acting:</div><ul>'
                    + "".join(f"<li>{html.escape(h)}</li>" for h in headlines) + "</ul></div>")
        else:
            news = '<div class="news"><div class="nt">No NSE filing or news today — verify manually.</div></div>'
        chart_id = f"chart{n}"
        candles = r.get("candles") or []
        if candles:
            charts[chart_id] = {"candles": candles, "entry": r["entry"], "stop": r["stop"],
                                "t1": r["target1"], "t2": r["target2"]}
        chart_div = f'<div class="chart" id="{chart_id}"></div>' if candles else ""
        cards.append(
            f'<div class="card {side}"><div class="chead">'
            f'<span class="rank">#{n}</span><span class="sym">{html.escape(r["symbol"])}</span>'
            f'{badges}<span class="score">score {r["score"]:.1f}/100</span></div>'
            f'<div class="grid">{grid}</div>{chart_div}'
            f'<div class="reason">{html.escape(r["reason"])}</div>{news}</div>')
    body = "".join(cards) if cards else (
        '<div class="card"><div class="chead"><span class="sym">No qualified trade</span></div>'
        '<div class="reason">No symbol cleared the RVOL + trend + net-R:R + range gates this run.</div></div>')
    meta = "".join(f'<span class="pill">{html.escape(m)}</span>' for m in [
        f"Market: {status}", f"Regime: {regime}",
        f"Breadth {breadth['advancers']}/{breadth['decliners']}",
        f"{coverage['qualified']} qualified of {coverage['scanned']} scanned"])
    chart_script = ""
    if charts:
        chart_script = (f'<script src="{_DASH_CDN}"></script>'
                        f'<script>const CHARTS={json.dumps(charts)};{_DASH_JS}</script>')
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>NSE Intraday — {html.escape(stamp)}</title><style>{_DASH_CSS}</style></head>'
            f'<body><div class="wrap"><h1>NSE Intraday Engine</h1>'
            f'<div class="sub">{html.escape(stamp)} · {html.escape(regime_detail)}</div>'
            f'<div class="meta">{meta}</div>{body}'
            f'<div class="foot">Personal research only — NOT advice. Manual execution; verify '
            f'price/liquidity/trigger before acting. Labels: CONFIRMED=triggered · PENDING=await '
            f'trigger · UNVERIFIED=stale/after-hours.</div></div>{chart_script}</body></html>')


def write_dashboard(*args):
    """Write the HTML dashboard and open it in the default browser."""
    DASHBOARD_PATH.write_text(build_dashboard_html(*args), encoding="utf-8")
    try:
        webbrowser.open(DASHBOARD_PATH.resolve().as_uri())
    except Exception as exc:
        logging.warning("Could not auto-open dashboard: %s", exc)
    logging.info("Dashboard written to %s", DASHBOARD_PATH.resolve())


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    source = "mock" if "--offline" in sys.argv else _arg_value("--source", os.getenv("SOURCE", "kite"))
    offline = source == "mock"
    init_db()
    lookback = env_int("LOOKBACK_DAYS", 5)
    src = get_source(source, lookback)
    if offline:
        logging.info("OFFLINE MODE: deterministic synthetic data; numbers are illustrative only.")
    else:
        logging.info("Data source: %s", source)

    # Holiday / after-hours guard. When the source can report live market state (NSE),
    # a scheduled run on a closed day (weekend, NSE holiday, after 15:30) exits here
    # instead of scanning stale data and firing notifications. --force overrides.
    if not offline and "--force" not in sys.argv:
        state_fn = getattr(src, "market_state", None)
        state = state_fn() if state_fn else ""
        if state and state.lower().startswith("close"):
            logging.info("NSE market state is '%s' — skipping scan (pass --force to override).", state)
            return

    imap = build_universe(src)
    symbols = sorted(imap.keys())
    news = load_news(set(symbols))
    snap, breadth = bulk_market_snapshot(src, symbols)
    if "--gainers" in sys.argv:
        print_gainers(snap)
        return
    regime, nifty_change, regime_detail = classify_regime(src, breadth)

    threshold = env_float("MIN_AVG_DAILY_TURNOVER_INR", 50000000)
    liquid = {s: row for s, row in snap.items() if row["turnover"] >= threshold}
    # Deep-scan every liquid name (quality over speed). The default cap is high
    # enough to cover the whole Nifty 500 liquid set.
    ranked_liquid = sorted(liquid, key=lambda s: liquid[s]["turnover"], reverse=True)
    deep = ranked_liquid[:env_int("MAX_DEEP_SCAN", 600)]
    # Yahoo serves candles from one cached batch, so no per-symbol throttle is needed;
    # only a real rate-limited API (Kite) needs the sleep.
    yahoo = source in ("yahoo", "yfinance", "free")
    sleep = 0 if (offline or yahoo) else env_float("HISTORICAL_SLEEP_SEC", 0.35)
    interval = os.getenv("CANDLE_INTERVAL", "5minute")
    end = datetime.now(TZ).replace(tzinfo=None)
    start = end - timedelta(days=max(lookback, 5))

    results = []
    for sym in deep:
        token = imap[sym]["instrument_token"]
        try:
            candles = src.historical(token, start, end, interval)
            row = score_candidate(sym, snap[sym], pd.DataFrame(candles),
                                  nifty_change, regime, offline, news)
            if row:
                results.append(row)
        except Exception as exc:
            logging.warning("Skipping %s after data/API error: %s", sym, exc)
        if sleep:
            time.sleep(sleep)

    # Sector strength: average day-change per sector across the universe. A stock
    # moving WITH a strong (long) / weak (short) sector is higher-conviction. Live-
    # only signal — the backtest can't replay this; the scorecard validates it.
    sector_vals = {}
    for s, row in snap.items():
        sec = SECTORS.get(s)
        if sec:
            sector_vals.setdefault(sec, []).append(row["day_change_pct"])
    sector_avg = {k: sum(v) / len(v) for k, v in sector_vals.items()}

    # Catalyst-led ranking. One NSE call maps every stock to its own filings today,
    # so a MATERIAL, symbol-tagged catalyst (results/order/acquisition/buyback/rating)
    # — not a fuzzy web-news keyword match — drives the catalyst score and re-ranks.
    # A qualified candidate with a real filing joins the shortlist even if its
    # technicals ranked just below the cutoff, so a genuine catalyst can surface.
    results.sort(key=lambda r: r["score"], reverse=True)
    buy_mode = "--buy" in sys.argv
    ann_fn = getattr(src, "announcements", None)
    ann_map = ann_fn() if ann_fn else {}
    cutoff = max(env_int("NEWS_SCAN_LIMIT", 15), env_int("BUY_MAX", 12))
    tech_short = results[:cutoff]
    cat_extra = [r for r in results[cutoff:]
                 if classify_announcements(ann_map.get(r["symbol"], []))[0]][:15]
    shortlist = tech_short + cat_extra
    for r in shortlist:
        material, direction, filing_texts = classify_announcements(ann_map.get(r["symbol"], []))
        r["catalyst"] = material
        if material:  # authoritative, symbol-tagged catalyst drives the score
            r["news_direction"], r["news_headlines"], r["catalyst_src"] = direction, filing_texts, "NSE filing"
        else:         # no verified catalyst; web news shown as context only, never scored
            r["news_headlines"] = fetch_live_news(r["symbol"])
            r["news_direction"], r["catalyst_src"] = news_direction(r["news_headlines"]), "web news (context)"
        aligned = ((r["news_direction"] == "BULLISH" and r["side"] == "LONG") or
                   (r["news_direction"] == "BEARISH" and r["side"] == "SHORT"))
        contra = ((r["news_direction"] == "BULLISH" and r["side"] == "SHORT") or
                  (r["news_direction"] == "BEARISH" and r["side"] == "LONG"))
        if r["catalyst"] and aligned:
            r["components"]["catalyst"] = 100.0   # filing agrees with the trade
        elif r["catalyst"] and contra:
            r["components"]["catalyst"] = 15.0    # filing fights the trade — demote
        elif r["catalyst"]:
            r["components"]["catalyst"] = 70.0    # material filing, direction unclear
        else:
            r["components"]["catalyst"] = 50.0    # no verified catalyst
        # Higher-timeframe alignment: reward trading with the daily trend, penalize
        # fighting it. Source-agnostic — sources without daily_trend() stay neutral.
        trend_fn = getattr(src, "daily_trend", None)
        r["daily_trend"] = trend_fn(r["symbol"]) if trend_fn else "FLAT"
        if ((r["daily_trend"] == "UP" and r["side"] == "LONG") or
                (r["daily_trend"] == "DOWN" and r["side"] == "SHORT")):
            r["components"]["price_structure"] = _clamp(r["components"]["price_structure"] + 20)
        # Sector strength: boost a pick moving with its sector (forward-test validated).
        r["sector"] = SECTORS.get(r["symbol"], "")
        r["sector_chg"] = sector_avg.get(r["sector"], 0.0)
        if ((r["side"] == "LONG" and r["sector_chg"] > 0.3) or
                (r["side"] == "SHORT" and r["sector_chg"] < -0.3)):
            r["components"]["relative_strength"] = _clamp(r["components"]["relative_strength"] + 15)
        r["score"] = sum(r["components"][k] * WEIGHTS[k] for k in WEIGHTS) / sum(WEIGHTS.values())
    # With-trend only (backtested to help): drop setups fighting a clear daily trend.
    shortlist = [r for r in shortlist if not (
        (r["daily_trend"] == "UP" and r["side"] == "SHORT") or
        (r["daily_trend"] == "DOWN" and r["side"] == "LONG"))]
    shortlist.sort(key=lambda r: r["score"], reverse=True)
    # Derive both views from the one enriched scan.
    # Diversify: avoid two correlated (same-sector) names in the shown picks.
    longs = [r for r in shortlist if r["side"] == "LONG" and r.get("day_change_pct", 0) > 0]
    buy_list = _diversify(longs, env_int("BUY_MAX", 3))
    top_list = _diversify(shortlist, env_int("MAX_CANDIDATES", 2))
    coverage = {"available": len(imap), "liquid": len(liquid),
                "scanned": len(deep), "qualified": len(shortlist)}
    stamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S IST")
    status = market_status(datetime.now(TZ))

    if buy_mode:  # focused: just the investable gainers
        featured = buy_list
        report = build_report(stamp, status, regime, regime_detail, breadth, coverage, buy_list)
    else:  # default: everything in one run
        featured = top_list
        report = build_combined(stamp, status, regime, regime_detail, breadth,
                                coverage, snap, buy_list, top_list)

    with sqlite3.connect(DB_PATH) as con:
        for r in featured:
            con.execute(
                "INSERT INTO signals_v2(created_at,symbol,side,label,price,entry,stop,"
                "target1,target2,qty,gross_rr,net_rr,rel_volume,score,regime,reason) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (stamp, r["symbol"], r["side"], r["label"], r["price"], r["entry"],
                 r["stop"], r["target1"], r["target2"], r["qty"], r["gross_rr"],
                 r["net_rr"], r["rel_volume"], r["score"], regime, r["reason"]))

    print(report)
    # Attach recent candles to the shown picks so the dashboard can chart them with
    # the entry/stop/target drawn on. Candles are already cached by the source, so
    # this is cheap (no extra network for Yahoo/NSE).
    for r in featured:
        try:
            r["candles"] = _candle_series(
                src.historical(imap[r["symbol"]]["instrument_token"], start, end, interval))
        except Exception as exc:
            logging.warning("No chart candles for %s: %s", r["symbol"], exc)
            r["candles"] = []
    write_dashboard(stamp, status, regime, regime_detail, breadth, coverage,
                    buy_list if buy_mode else top_list)
    notify_telegram(report)
    notify_email(f"NSE intraday engine — {stamp}", report)


if __name__ == "__main__":
    main()
