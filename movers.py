"""Intraday MARKET MOVER scanner — finds the day's strongest movers across ALL of NSE and
tells you WHICH stocks to trade, instead of you guessing one in the extension.

Pulls NSE's own live top gainers / losers, ranks them by move size + stage (room left to
run) + a news/filing catalyst, and pushes the top few to your phone + email. You then open
those names in the extension and let Signals/VIM work the entry.

HONEST SCOPE: a stock already up ~10% has mostly moved — this FAVOURS movers still early
(2–7%) with a catalyst, and down-weights exhausted spikes, but it does NOT predict the next
move. It points you at where the real action + volume is; the entry/stop/exit stays with
the deterministic Signals engine and your judgement. No prediction, no guarantee.

Run: python movers.py                 (prints the shortlist)
     python movers.py --ntfy --email   (also push)
The scheduler runs it intraday automatically when a market-hours slot fires.
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

from datasource import NSESource, TZ


def stage(p):
    a = abs(p)
    return "early" if a < 4 else "building" if a < 7 else "extended"


def rank_key(x):
    s = abs(x["pChange"])
    if x["stage"] == "extended":
        s *= 0.5          # already moved — down-weight (don't chase the top)
    if x["catalyst"]:
        s *= 1.3          # a real reason to move
    if 2 <= abs(x["pChange"]) <= 7:
        s *= 1.2          # sweet spot: momentum + room
    return s


MIN_PRICE = float(os.getenv("MOVERS_MIN_PRICE", "30"))   # skip penny stocks
MIN_MOVE = 1.5                                            # must have actually moved
MAX_MOVE = 20.0                                           # above this = split/bonus/circuit, not a trade


def scan(top_n=6):
    src = NSESource()
    state = src.market_state()
    mv = src.top_movers()
    anns = {}
    try:
        anns = src.announcements(days=1)
    except Exception:
        pass

    seen, pool = set(), []
    for r in mv.get("gainers", []) + mv.get("losers", []):
        sym, p, ltp = r["symbol"], r["pChange"], r["ltp"]
        if sym in seen or ltp < MIN_PRICE or not (MIN_MOVE <= abs(p) <= MAX_MOVE):
            continue      # drop pennies, non-movers, and corporate-action anomalies
        seen.add(sym)
        cat = ""
        if sym in anns and anns[sym]:
            cat = (anns[sym][0].get("desc") or "filing")[:40]
        pool.append({**r, "side": "LONG" if p > 0 else "SHORT", "stage": stage(p), "catalyst": cat})
    return state, sorted(pool, key=rank_key, reverse=True)[:top_n]


def format_report(state, picks):
    lines = [f"# 🔥 NSE movers — {datetime.now(TZ):%d %b %H:%M}  (market {state or '?'})", ""]
    if not picks:
        lines.append("No movers returned (market closed or NSE unreachable).")
        return "\n".join(lines)
    for i, p in enumerate(picks, 1):
        arrow = "▲" if p["side"] == "LONG" else "▼"
        cat = f" · {p['catalyst']}" if p["catalyst"] else ""
        lines.append(f"{i}. {arrow} {p['symbol']}  {p['pChange']:+.1f}%  ₹{p['ltp']:,.1f}  [{p['stage']}]{cat}")
    lines.append("")
    lines.append("_Open these in the extension; let Signals/VIM work the entry. Favour 'early'/'building' "
                 "with a catalyst; 'extended' has mostly moved. Not a prediction — paper-trade, honour the stop._")
    return "\n".join(lines)


def push(report, picks):
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if "--email" in flags:
        try:
            import bot
            bot.notify_email("NSE movers: " + ", ".join(p["symbol"] for p in picks[:6]), report)
            print("[emailed]", file=sys.stderr)
        except Exception as e:
            print(f"[email failed: {e}]", file=sys.stderr)
    if "--ntfy" in flags and os.getenv("NTFY_TOPIC", "").strip():
        try:
            head = "\n".join(l for l in report.splitlines() if l and not l.startswith("_") and not l.startswith("#"))
            urllib.request.urlopen(urllib.request.Request(
                "https://ntfy.sh/" + os.getenv("NTFY_TOPIC").strip(), data=head.encode(),
                headers={"Title": "🔥 NSE movers", "Priority": "default"}), timeout=10)
            print("[pushed]", file=sys.stderr)
        except Exception as e:
            print(f"[ntfy failed: {e}]", file=sys.stderr)


def main():
    state, picks = scan()
    report = format_report(state, picks)
    print(report)
    push(report, picks)


if __name__ == "__main__":
    main()
