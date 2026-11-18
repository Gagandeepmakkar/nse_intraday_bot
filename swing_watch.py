"""Intraday trigger-watcher for the morning swing shortlist.

Reads swing_watch.json (written by swing_scan), checks each setup's LIVE price
against its levels, and pushes a phone alert ONLY on a state change — a watch name
entering its buy zone, a position hitting T1/T2, or breaking its stop. Status is
persisted so each event fires once. Runs every ~15 min in market hours (cron).

This is the live-execution layer: you plan once in the morning, and this watches
the handful of names for you, hands-free — reacting by RULES, not tape-chasing.

Run: python swing_watch.py            (print)
     python swing_watch.py --ntfy     (also push to phone)
"""
import json
import os
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import requests

import oi

TZ = ZoneInfo("Asia/Kolkata")
STATE = "swing_watch.json"


def _ltps(symbols):
    """Batch live LTP for symbols via Upstox. {symbol: price}."""
    keys = {}
    for s in symbols:
        ik = oi.instrument_key(s)
        if ik:
            keys[ik] = s
    out, iks = {}, list(keys)
    for i in range(0, len(iks), 50):
        try:
            r = requests.get("https://api.upstox.com/v2/market-quote/ltp",
                             params={"instrument_key": ",".join(iks[i:i + 50])},
                             headers=oi._headers(), timeout=15)
            for v in (r.json().get("data") or {}).values():
                s = keys.get(v.get("instrument_token"))
                if s:
                    out[s] = v.get("last_price")
        except Exception:
            pass
    return out


def _event(su, price):
    """(new_status, message) on a state change, else (None, None)."""
    side, st = su["side"], su.get("status")
    long = side == "LONG"
    stop, t1, t2 = su.get("stop"), su.get("t1"), su.get("t2")
    if st == "watch":
        bz = su.get("buy_zone")
        # Trigger direction depends on setup, not just side: a breakout LONG trigger is
        # ABOVE price (fire when price rises TO it); a pullback LONG trigger is BELOW
        # price (fire when price falls TO it). Breakdown/bounce mirror for shorts.
        setup = (su.get("setup") or "").lower()
        breakout = setup.startswith("breakout") or setup.startswith("breakdown")
        if bz:
            if breakout:
                fire = (price >= bz * 0.995) if long else (price <= bz * 1.005)
            else:
                fire = (price <= bz * 1.005) if long else (price >= bz * 0.995)
            if fire:
                return "active", (f"🎯 {'BREAK' if breakout else 'BUY ZONE'} {su['symbol']} @₹{price} — "
                                  f"{side} near ₹{bz}, stop ₹{stop}, T1 ₹{t1}")
        return None, None
    if st in ("active", "t1"):
        if (long and price <= stop) or (not long and price >= stop):
            return "done", f"🛑 STOP {su['symbol']} @₹{price} — exit. Plan done (−1R)."
        if t2 and ((long and price >= t2) or (not long and price <= t2)):
            return "done", f"✅ T2 {su['symbol']} @₹{price} — final target hit, book the rest."
        if st == "active" and t1 and ((long and price >= t1) or (not long and price <= t1)):
            return "t1", f"✅ T1 {su['symbol']} @₹{price} — book 1/3, trail stop to breakeven."
    return None, None


def push(msg):
    topic = os.getenv("NTFY_TOPIC", "").strip()
    if not topic:
        return
    try:
        urllib.request.urlopen(urllib.request.Request(
            "https://ntfy.sh/" + topic, data=msg.encode("utf-8"),
            headers={"Title": "Swing trigger", "Tags": "rotating_light"}), timeout=10)
    except Exception:
        pass


def run(do_push=False):
    if not os.path.exists(STATE):
        return ["no swing_watch.json — run swing_scan first"]
    data = json.load(open(STATE))
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    if data.get("date") != today:
        return [f"swing_watch.json is stale (dated {data.get('date')}, today {today}) — "
                f"this morning's scan didn't write it; not watching."]
    live = [s for s in data.get("setups", []) if s.get("status") != "done"]
    if not live:
        return ["no active setups to watch"]
    ltp = _ltps([s["symbol"] for s in live])
    lines, changed = [], False
    for su in data["setups"]:
        if su.get("status") == "done":
            continue
        p = ltp.get(su["symbol"])
        if p is None:
            continue
        ns, msg = _event(su, p)
        if ns:
            su["status"] = ns
            changed = True
            lines.append(msg)
            if do_push:
                push(msg)
    if changed:
        json.dump(data, open(STATE, "w"))
    return lines or [f"{datetime.now(TZ):%H:%M} — no triggers ({len(live)} watching)"]


if __name__ == "__main__":
    for l in run(do_push=("--ntfy" in sys.argv)):
        print(l)
