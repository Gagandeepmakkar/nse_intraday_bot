"""Security-wise DELIVERY % from NSE's free daily bhavcopy (sec_bhavdata_full CSV on the
archive host — not the anti-bot /api). High delivery % = buyers taking delivery (real
accumulation), not intraday churn — a genuine conviction signal. Cached per day; degrades
to {} on any error so the scan never breaks.

delivery_map() -> {SYMBOL: deliv_per_float};  deliv_pct(symbol) -> float|None
"""
import io
import json
import pathlib
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

TZ = ZoneInfo("Asia/Kolkata")
_CACHE = pathlib.Path(".delivery.json")
_MAP = None
URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{}.csv"


def _fetch_csv():
    """Most recent available sec_bhavdata_full (published after close ~6pm). Tries today
    back 7 days, skipping weekends. Returns CSV text or None."""
    try:
        from curl_cffi import requests as cr
    except Exception:
        return None
    s = cr.Session(impersonate="chrome")
    s.headers.update({"Referer": "https://www.nseindia.com/", "Accept": "text/csv,*/*"})
    try:
        s.get("https://www.nseindia.com/", timeout=15)   # prime cookies
    except Exception:
        pass
    for back in range(0, 8):
        d = datetime.now(TZ) - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        try:
            r = s.get(URL.format(d.strftime("%d%m%Y")), timeout=20)
            if r.status_code == 200 and "SYMBOL" in r.text[:300]:
                return r.text
        except Exception:
            pass
    return None


def delivery_map():
    global _MAP
    if _MAP is not None:
        return _MAP
    if _CACHE.exists() and time.time() - _CACHE.stat().st_mtime < 43200:  # 12h
        try:
            _MAP = json.loads(_CACHE.read_text())
            return _MAP
        except Exception:
            pass
    m = {}
    txt = _fetch_csv()
    if txt:
        try:
            df = pd.read_csv(io.StringIO(txt))
            df.columns = [c.strip() for c in df.columns]   # columns have leading spaces
            dp_col = next((c for c in df.columns if c.replace(" ", "").upper() == "DELIV_PER"), None)
            if "SERIES" in df.columns:
                df = df[df["SERIES"].astype(str).str.strip() == "EQ"]
            if dp_col and "SYMBOL" in df.columns:
                for _, row in df.iterrows():
                    try:
                        m[str(row["SYMBOL"]).strip().upper()] = float(str(row[dp_col]).strip())
                    except Exception:
                        continue
        except Exception:
            pass
    _MAP = m
    try:
        _CACHE.write_text(json.dumps(m))
    except Exception:
        pass
    return _MAP


def deliv_pct(symbol):
    return delivery_map().get(symbol.upper().strip())


if __name__ == "__main__":
    import sys
    m = delivery_map()
    print(f"delivery map: {len(m)} symbols")
    for s in (sys.argv[1:] or ["RELIANCE", "SBIN", "TCS"]):
        print(f"  {s}: {deliv_pct(s)}% delivery")
