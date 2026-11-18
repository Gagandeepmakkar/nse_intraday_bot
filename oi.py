"""Options Open-Interest levels via the free Upstox market-data API.

The missing structural piece: real support/resistance from where option writers sit
(the call wall = resistance, the put wall = support — the levels a trader "just
knows"). NSE's free option-chain API is anti-bot blocked and the Groww MCP can't be
called from a script; Upstox's free data API CAN.

Auth: UPSTOX_ACCESS_TOKEN in .env — a data-only *extended* token (market data +
option chain, valid ~1 year). It CANNOT read the account or place orders (profile
returns 401), so it is money-safe by construction.

walls(symbol) -> near-spot call wall, put wall, PCR, max-pain, so levels.py / read.py
can show the OI-backed S/R alongside the price-structure ladder.
"""
import gzip
import json
import os
import pathlib
import time

import requests
from dotenv import load_dotenv

load_dotenv()

BASE = "https://api.upstox.com/v2"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
_CACHE = pathlib.Path(".upstox_instruments.json")  # gitignored; refreshed daily
_MAP = None


def _headers():
    tok = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()
    if not tok:
        raise RuntimeError("UPSTOX_ACCESS_TOKEN missing in .env")
    return {"Authorization": f"Bearer {tok}", "Accept": "application/json"}


def _load_map():
    """{trading_symbol -> instrument_key} for NSE equities + indices, cached 1 day."""
    global _MAP
    if _MAP is not None:
        return _MAP
    if _CACHE.exists() and time.time() - _CACHE.stat().st_mtime < 86400:
        _MAP = json.loads(_CACHE.read_text())
        return _MAP
    data = json.loads(gzip.decompress(requests.get(INSTRUMENTS_URL, timeout=60).content))
    m = {d["trading_symbol"].upper(): d["instrument_key"]
         for d in data
         if d.get("segment") in ("NSE_EQ", "NSE_INDEX") and d.get("trading_symbol")}
    _CACHE.write_text(json.dumps(m))
    _MAP = m
    return m


def instrument_key(symbol):
    return _load_map().get(symbol.upper().strip())


def _max_pain(calls, puts):
    """Strike that minimises total option-writer payout at expiry (where price pins)."""
    strikes = sorted(set(calls) | set(puts))
    if not strikes:
        return None

    def payout(settle):
        tot = 0.0
        for k in strikes:
            tot += calls.get(k, 0) * max(0.0, settle - k)   # call writers pay when settle>k
            tot += puts.get(k, 0) * max(0.0, k - settle)    # put writers pay when settle<k
        return tot

    return min(strikes, key=payout)


def walls(symbol, band_pct=4.0, expiry=None):
    """Near-spot OI walls for `symbol`. band_pct = how far from spot to look for the
    *actionable* wall (far OTM strikes can hold the global-max OI but aren't today's
    S/R). Returns dict or None if the name has no options / token fails."""
    ik = instrument_key(symbol)
    if not ik:
        return None
    h = _headers()
    if not expiry:
        rc = requests.get(f"{BASE}/option/contract", params={"instrument_key": ik}, headers=h, timeout=20)
        exps = sorted({d["expiry"] for d in (rc.json().get("data") or []) if d.get("expiry")})
        if not exps:
            return None
        expiry = exps[0]
    oc = requests.get(f"{BASE}/option/chain", params={"instrument_key": ik, "expiry_date": expiry},
                      headers=h, timeout=20)
    rows = oc.json().get("data") or []
    if not rows:
        return None
    spot = rows[0].get("underlying_spot_price")
    calls, puts, tot_ce, tot_pe = {}, {}, 0, 0
    for r in rows:
        k = r["strike_price"]
        calls[k] = ((r.get("call_options") or {}).get("market_data") or {}).get("oi") or 0
        puts[k] = ((r.get("put_options") or {}).get("market_data") or {}).get("oi") or 0
        tot_ce += calls[k]
        tot_pe += puts[k]
    band = spot * band_pct / 100 if spot else None
    near_calls = {k: v for k, v in calls.items() if spot and k >= spot and abs(k - spot) <= band and v > 0}
    near_puts = {k: v for k, v in puts.items() if spot and k <= spot and abs(k - spot) <= band and v > 0}
    call_wall = max(near_calls, key=near_calls.get) if near_calls else (max(calls, key=calls.get) if calls else None)
    put_wall = max(near_puts, key=near_puts.get) if near_puts else (max(puts, key=puts.get) if puts else None)
    return {
        "symbol": symbol.upper(), "spot": spot, "expiry": expiry,
        "pcr": round(tot_pe / tot_ce, 3) if tot_ce else None,
        "call_wall": call_wall, "call_wall_oi": calls.get(call_wall),
        "put_wall": put_wall, "put_wall_oi": puts.get(put_wall),
        "max_pain": _max_pain(calls, puts),
    }


if __name__ == "__main__":
    import sys
    for s in (sys.argv[1:] or ["RELIANCE"]):
        w = walls(s)
        if not w:
            print(f"{s}: no option data (not in F&O?)")
            continue
        print(f"{w['symbol']}  spot ₹{w['spot']}  exp {w['expiry']}  PCR {w['pcr']}")
        print(f"  resistance (call wall): ₹{w['call_wall']}  OI {w['call_wall_oi']:,.0f}")
        print(f"  support   (put wall):   ₹{w['put_wall']}  OI {w['put_wall_oi']:,.0f}")
        print(f"  max pain: ₹{w['max_pain']}")
