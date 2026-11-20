"""Best-effort SECTOR strength: is the stock's sector index trending the same way as the
trade? Trade the strongest stock in the strongest sector. yfinance gives the stock's sector
(cached in .sectors.json, amortised); the NSE sector index trend comes from free Upstox
daily candles. Degrades to None (neutral) for sectors that don't map cleanly — never blocks.

sector_trend(symbol) -> "UP" | "DOWN" | "FLAT" | None
"""
import json
import pathlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

import oi

TZ = ZoneInfo("Asia/Kolkata")
_SEC_CACHE = pathlib.Path(".sectors.json")
_SEC = None
_IDX_TREND = {}

# Only the yfinance sectors that map cleanly to an NSE sector index; others -> neutral.
YF_TO_IDX = {
    "Technology": "Nifty IT",
    "Energy": "Nifty Energy",
    "Healthcare": "Nifty Pharma",
    "Basic Materials": "Nifty Metal",
    "Consumer Defensive": "Nifty FMCG",
    "Real Estate": "Nifty Realty",
    "Financial Services": "Nifty Bank",   # banks dominate Indian financials; this key resolves
}


def _load():
    global _SEC
    if _SEC is None:
        try:
            _SEC = json.loads(_SEC_CACHE.read_text())
        except Exception:
            _SEC = {}
    return _SEC


def sector_of(symbol):
    """yfinance sector for an NSE symbol, cached forever (sectors rarely change)."""
    sym = symbol.upper().strip()
    sec = _load()
    if sym in sec:
        return sec[sym]
    val = None
    try:
        import yfinance as yf
        val = yf.Ticker(f"{sym}.NS").info.get("sector")
    except Exception:
        val = None
    sec[sym] = val
    try:
        _SEC_CACHE.write_text(json.dumps(sec))
    except Exception:
        pass
    return val


def _idx_trend(idx_name):
    if idx_name in _IDX_TREND:
        return _IDX_TREND[idx_name]
    key = oi.instrument_key(idx_name) or f"NSE_INDEX|{idx_name}"
    trend = None
    try:
        to = datetime.now(TZ).strftime("%Y-%m-%d")
        frm = (datetime.now(TZ) - timedelta(days=120)).strftime("%Y-%m-%d")
        r = requests.get(f"https://api.upstox.com/v3/historical-candle/{key}/days/1/{to}/{frm}",
                         headers=oi._headers(), timeout=20)
        cs = [c[4] for c in reversed((r.json().get("data") or {}).get("candles", []))]
        c = pd.Series(cs)
        if len(c) >= 50:
            e20 = c.ewm(span=20, adjust=False).mean().iloc[-1]
            e50 = c.ewm(span=50, adjust=False).mean().iloc[-1]
            last = c.iloc[-1]
            trend = "UP" if last > e20 > e50 else "DOWN" if last < e20 < e50 else "FLAT"
    except Exception:
        trend = None
    _IDX_TREND[idx_name] = trend
    return trend


def sector_trend(symbol):
    idx = YF_TO_IDX.get(sector_of(symbol))
    return _idx_trend(idx) if idx else None


if __name__ == "__main__":
    import sys
    for s in (sys.argv[1:] or ["TCS", "HDFCBANK", "SUNPHARMA", "RELIANCE", "TATASTEEL"]):
        print(f"{s}: sector={sector_of(s)!r} -> index trend {sector_trend(s)}")
