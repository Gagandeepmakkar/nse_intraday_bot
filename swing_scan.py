"""Morning SWING scan — ranks the universe through the ONE brain (swing.py) and
emails/pushes the best BUY/SELL setups with entry, stop and targets.

Single source of truth: every line here is a swing.score() verdict — identical to
what the extension panel will show for the same stock. No separate logic, no
divergence. On an extended market (nothing clean) it says SIT OUT, honestly, with
the regime context — it will not manufacture a trade.

Two-pass for speed: fast-score the whole universe (1 API call/stock), then re-score
only the top names with full detail (live price + option walls).

Run: python swing_scan.py            (print)
     python swing_scan.py --ntfy --email
"""
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

import swing

TZ = ZoneInfo("Asia/Kolkata")


def _universe(limit=None):
    p = os.getenv("SYMBOLS_FILE", "symbols.txt")
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            s = line.strip().upper()
            if s and not s.startswith("#"):
                out.append(s)
    return out[:limit] if limit else out


def scan(limit=None, enrich_top=12):
    syms = _universe(limit)
    fast = []
    for s in syms:
        try:
            r = swing.score(s, fast=True)
        except Exception:
            continue
        if r.get("score") is not None:
            fast.append(r)
    fast.sort(key=lambda r: r.get("score", 0), reverse=True)
    # Re-score the most promising names with full detail (live price + OI walls).
    top = []
    for r in fast[:enrich_top]:
        try:
            top.append(swing.score(r["symbol"]))
        except Exception:
            top.append(r)
    top.sort(key=lambda r: r.get("score", 0), reverse=True)
    buys = [r for r in top if r.get("action") in ("BUY", "SELL")]
    watch = [r for r in top if r.get("action") == "WAIT"][:4]
    return {"scanned": len(fast), "regime": swing._nifty_ret()[1], "buys": buys, "watch": watch}


def _line(r):
    lv = r.get("levels") or {}
    ev = r.get("evidence") or {}
    rs = ev.get("rel_strength_vs_nifty_pct")
    why = f"trend {ev.get('trend','?')}, RS {rs:+.1f}% vs NIFTY, RSI {ev.get('rsi','?')}" if rs is not None else ""
    oi = ev.get("oi_walls")
    oistr = f" · OI S ₹{oi['put']:.0f}/R ₹{oi['call']:.0f}" if oi and oi.get("put") and oi.get("call") else ""
    eta = lv.get("eta_days")
    etastr = f" · ~{eta} trading day{'s' if eta and eta != 1 else ''} to T1" if eta else ""
    return (f"{r['symbol']} — {r['action']} ({r['side']}, score {r['score']})\n"
            f"   entry ₹{lv.get('entry')} · stop ₹{lv.get('stop')} · T1 ₹{lv.get('target1')} ({lv.get('t1_label','')}) · "
            f"T2 ₹{lv.get('target2')} · R:R {lv.get('rr')}{etastr}\n"
            f"   why: {why}{oistr}")


def report(res):
    out = [f"# 📈 Swing scan — {datetime.now(TZ):%d %b %Y %H:%M}",
           f"NIFTY regime: {res['regime']} · {res['scanned']} names scanned", ""]
    if res["buys"]:
        out.append(f"**{len(res['buys'])} setup(s) — entry/stop/target below:**")
        for r in res["buys"]:
            out.append(_line(r))
            out.append("")
    else:
        out.append("**No clean swing setup today — SIT OUT.** Nothing cleared the score + "
                   "reward:risk gate (market likely extended or choppy). Forcing a trade here is "
                   "how edges die. Closest names to watch for a pullback entry:")
        out.append("")
        for r in res["watch"]:
            lv = r.get("levels") or {}
            ev = r.get("evidence") or {}
            out.append(f"- {r['symbol']} (score {r['score']}, {ev.get('trend','?')}, RSI {ev.get('rsi','?')}) "
                       f"— needs pullback; would buy near ₹{lv.get('stop')}–support, not here ({r.get('note','')})")
    out.append("")
    out.append("_One brain: identical to the extension panel's call for each name. Swing/daily. "
               "Paper first; honour the stop. Not a prediction — a framed setup. No proven edge yet (audit.py)._")
    return "\n".join(out)


def push(text, res):
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    subj = ("Swing: " + ", ".join(f"{r['action']} {r['symbol']}" for r in res["buys"][:4])) if res["buys"] \
        else "Swing scan: no setup — sit out"
    if "--email" in flags:
        try:
            import bot
            bot.notify_email(subj, text)
            print("[emailed]", file=sys.stderr)
        except Exception as e:
            print(f"[email failed: {e}]", file=sys.stderr)
    if "--ntfy" in flags and os.getenv("NTFY_TOPIC", "").strip():
        try:
            head = "\n".join(l for l in text.splitlines() if l and not l.startswith("#") and not l.startswith("_") and not l.startswith("   "))
            urllib.request.urlopen(urllib.request.Request(
                "https://ntfy.sh/" + os.getenv("NTFY_TOPIC").strip(), data=head.encode("utf-8"),
                headers={"Title": subj, "Tags": "chart_with_upwards_trend"}), timeout=10)
            print("[pushed]", file=sys.stderr)
        except Exception as e:
            print(f"[ntfy failed: {e}]", file=sys.stderr)


def _persist(res):
    """Write the day's setups to swing_watch.json so swing_watch.py can track live
    triggers/stops/targets. BUY/SELL = active positions to manage; WAIT = watch names
    whose buy_zone (nearest support/resistance) we alert on when price reaches it."""
    import json
    setups = []
    for r in res["buys"]:
        lv = r.get("levels") or {}
        setups.append({"symbol": r["symbol"], "side": r["side"], "entry": lv.get("entry"),
                       "stop": lv.get("stop"), "t1": lv.get("target1"), "t2": lv.get("target2"),
                       "status": "active"})
    for r in res["watch"]:
        lv = r.get("levels") or {}
        ev = r.get("evidence") or {}
        sup = (ev.get("nearest_support") or {}).get("price") if r.get("side") == "LONG" else (ev.get("nearest_resistance") or {}).get("price")
        setups.append({"symbol": r["symbol"], "side": r["side"], "entry": lv.get("entry"),
                       "stop": lv.get("stop"), "t1": lv.get("target1"), "t2": lv.get("target2"),
                       "buy_zone": sup, "status": "watch"})
    try:
        json.dump({"date": datetime.now(TZ).strftime("%Y-%m-%d"), "setups": setups},
                  open("swing_watch.json", "w"))
    except Exception:
        pass
    # Append the actionable BUY/SELL calls to the append-only ledger so swing_audit.py
    # can grade them automatically against real price action later. Dedup per day.
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    try:
        seen = set()
        if os.path.exists("swing_ledger.jsonl"):
            for line in open("swing_ledger.jsonl"):
                try:
                    e = json.loads(line)
                    seen.add((e["date"], e["symbol"], e["side"]))
                except Exception:
                    pass
        with open("swing_ledger.jsonl", "a") as f:
            for r in res["buys"]:
                lv = r.get("levels") or {}
                if (today, r["symbol"], r["side"]) in seen:
                    continue
                f.write(json.dumps({"date": today, "symbol": r["symbol"], "side": r["side"],
                                    "entry": lv.get("entry"), "stop": lv.get("stop"),
                                    "t1": lv.get("target1"), "t2": lv.get("target2"),
                                    "eta": lv.get("eta_days")}) + "\n")
    except Exception:
        pass


def main():
    limit = None
    for a in sys.argv[1:]:
        if a.startswith("--limit="):
            limit = int(a.split("=", 1)[1])
    res = scan(limit=limit)
    text = report(res)
    print(text)
    _persist(res)
    push(text, res)


if __name__ == "__main__":
    main()
