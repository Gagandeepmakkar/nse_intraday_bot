"""Automated SWING audit — grades every swing call the system issued against REAL
price action (free Upstox daily candles), with zero manual input.

For each BUY/SELL in swing_ledger.jsonl: assume entry at the issued entry price, then
walk the daily candles forward and record which came FIRST — T1 (win, +R:R) or stop
(-1R) (same-day tie → stop, the conservative call). Unresolved calls are OPEN with
current unrealized R. Also compares EXPECTED days-to-target (eta) vs ACTUAL days, so
you learn whether the timing estimate is calibrated.

Reports win-rate, expectancy and the per-call ledger daily — like audit.py but for
the swing engine, fully automatic. This is how we learn if it actually works.

Run: python swing_audit.py [--ntfy] [--email]
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

import oi
import swing

TZ = ZoneInfo("Asia/Kolkata")
LEDGER = "swing_ledger.jsonl"


def _load():
    if not os.path.exists(LEDGER):
        return []
    seen = {}
    for line in open(LEDGER):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            seen[(e["date"], e["symbol"], e["side"])] = e  # dedup, keep last
        except Exception:
            pass
    return list(seen.values())


def _grade(e):
    ik = oi.instrument_key(e["symbol"])
    if not ik:
        return None
    issue = datetime.strptime(e["date"], "%Y-%m-%d").date()
    span = (datetime.now(TZ).date() - issue).days + 6
    df = swing._daily(ik, days=max(span, 30))
    if df.empty:
        return None
    rows = list(df[df["date"].dt.date > issue].iterrows())  # completed bars after issue
    long = e["side"] == "LONG"
    entry, stop, t1, trig = e["entry"], e["stop"], e["t1"], e.get("trigger")
    if None in (entry, stop, t1):
        return None
    # Issued today (or no completed bar after it yet) — PENDING, not a graded trade.
    # (Otherwise a conditional order gets mislabeled "never triggered" the day it's made.)
    if not rows:
        return {"result": "PENDING", "R": 0.0, "days": 0, "on": e["date"]}
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    win_R = round(abs(t1 - entry) / risk, 2)
    # Conditional order: only fills if price reached the trigger. If it never did,
    # it was never a trade (NOFILL), so it must not count as a win or a loss. The fill
    # direction depends on setup: breakout/breakdown triggers sit on the momentum side
    # (price rises/falls INTO them), pullback/bounce on the near side.
    setup = (e.get("setup") or "").lower()
    breakout = setup.startswith("breakout") or setup.startswith("breakdown")
    start = 0
    if trig is not None:
        def _filled(d):
            if breakout:
                return (d["high"] >= trig) if long else (d["low"] <= trig)
            return (d["low"] <= trig) if long else (d["high"] >= trig)
        hit = next((i for i, (_, d) in enumerate(rows) if _filled(d)), None)
        if hit is None:
            return {"result": "NOFILL", "R": 0.0, "days": len(rows),
                    "on": str(df["date"].iloc[-1].date())}
        start = hit
    for day_i, (_, d) in enumerate(rows[start:], start=1):
        hi, lo = d["high"], d["low"]
        hit_stop = (lo <= stop) if long else (hi >= stop)
        hit_t1 = (hi >= t1) if long else (lo <= t1)
        if hit_stop:  # conservative: if both in one bar, assume stop first
            return {"result": "STOP", "R": -1.0, "days": day_i, "on": str(d["date"].date())}
        if hit_t1:
            return {"result": "T1", "R": win_R, "days": day_i, "on": str(d["date"].date())}
    last = float(df["close"].iloc[-1])
    unreal = round(((last - entry) / risk) if long else ((entry - last) / risk), 2)
    return {"result": "OPEN", "R": unreal, "days": len(rows) - start, "on": str(df["date"].iloc[-1].date())}


def _shadow(e):
    """Pullback-vs-momentum study for one call. Grades TWO entries over the real price path:
      momentum = buy at the scan price the day it was issued (participate immediately)
      pullback = buy at the trigger only if price actually dipped there (the current logic)
    Returns (mom_pct, pb_pct) in %, or None if not gradeable yet. Lets us learn, over weeks,
    whether buying the dip or buying the signal makes more money — then re-rank on evidence."""
    ik = oi.instrument_key(e["symbol"])
    scan = e.get("scan_px")
    if not ik or not scan:
        return None
    issue = datetime.strptime(e["date"], "%Y-%m-%d").date()
    df = swing._daily(ik, days=max((datetime.now(TZ).date() - issue).days + 6, 30))
    if df.empty:
        return None
    rows = list(df[df["date"].dt.date > issue].iterrows())
    if not rows:
        return None  # too early — no completed bar after issue
    long = e["side"] == "LONG"
    stop, t1, trig = e["stop"], e["t1"], e.get("trigger") or e["entry"]
    sgn = 1 if long else -1

    def grade(entry_px):
        for _, d in rows:
            hi, lo = d["high"], d["low"]
            if (long and lo <= stop) or (not long and hi >= stop):
                return (stop / entry_px - 1) * 100 * sgn
            if (long and hi >= t1) or (not long and lo <= t1):
                return (t1 / entry_px - 1) * 100 * sgn
        return (float(df["close"].iloc[-1]) / entry_px - 1) * 100 * sgn

    mom = grade(scan)
    breakout = (e.get("setup") or "").lower().startswith(("breakout", "breakdown"))
    def filled(d):
        hi, lo = d["high"], d["low"]
        if breakout:
            return (hi >= trig) if long else (lo <= trig)
        return (lo <= trig) if long else (hi >= trig)
    pb = grade(trig) if any(filled(d) for _, d in rows) else 0.0  # 0 = never filled, no trade
    return mom, pb


def audit():
    graded = []
    for e in sorted(_load(), key=lambda x: x["date"]):
        g = _grade(e)
        if g:
            graded.append({**e, **g})
    closed = [g for g in graded if g["result"] in ("STOP", "T1")]
    openp = [g for g in graded if g["result"] == "OPEN"]
    pending = [g for g in graded if g["result"] == "PENDING"]
    return graded, closed, openp, pending


def report():
    graded, closed, openp, pending = audit()
    out = [f"# 📒 Swing audit — {datetime.now(TZ):%d %b %Y}"]
    if not graded:
        out.append("No swing calls issued yet — nothing to grade. (Scan has been SIT-OUT.)")
        return "\n".join(out)
    if pending:
        out.append(f"_{len(pending)} call(s) issued today — pending (awaiting trigger/outcome), not yet graded._")
    if closed:
        wins = [c for c in closed if c["R"] > 0]
        tot = sum(c["R"] for c in closed)
        exp = tot / len(closed)
        wr = 100 * len(wins) / len(closed)
        # expected vs actual days (calibration)
        pairs = [(c.get("eta"), c["days"]) for c in closed if c.get("eta")]
        cal = ""
        if pairs:
            avg_exp = sum(p[0] for p in pairs) / len(pairs)
            avg_act = sum(p[1] for p in pairs) / len(pairs)
            cal = f" · timing: expected ~{avg_exp:.1f}d, actual ~{avg_act:.1f}d"
        out.append(f"**Closed {len(closed)} · win {wr:.0f}% · total {tot:+.2f}R · exp {exp:+.2f}R/trade{cal}**")
    else:
        out.append("**No closed swing calls yet** (all still open).")
    out.append("")
    out.append("**Ledger (auto-graded vs real prices):**")
    run = 0.0
    for c in sorted(closed, key=lambda x: x["date"]):
        run += c["R"]
        eta = f", exp ~{c['eta']}d" if c.get("eta") else ""
        out.append(f"- {c['date']} {c['symbol']} {c['side']} @{c['entry']} → {c['result']} "
                   f"{c['R']:+.2f}R in {c['days']}d{eta} (cum {run:+.2f}R)")
    for o in openp:
        out.append(f"- {o['date']} {o['symbol']} {o['side']} @{o['entry']} → OPEN "
                   f"({o['R']:+.2f}R unreal, {o['days']}d in)")
    nofill = [g for g in graded if g["result"] == "NOFILL"]
    if nofill:
        out.append(f"- {len(nofill)} conditional order(s) never triggered (price didn't reach the level) — not trades.")
    out.append("")
    # Shadow study: momentum (buy at signal) vs pullback (buy the dip) — learn which wins.
    shadows = [s for s in (_shadow(e) for e in sorted(_load(), key=lambda x: x["date"])) if s]
    if shadows:
        mom = sum(m for m, _ in shadows)
        pb = sum(p for _, p in shadows)
        verdict = "momentum ahead" if mom > pb + 5 else "pullback ahead" if pb > mom + 5 else "roughly even"
        out.append(f"**🔬 Entry study ({len(shadows)} gradeable): buy-at-signal {mom:+.1f}% total · "
                   f"buy-the-dip {pb:+.1f}% total → {verdict}**")
        out.append("_(When this has 20+ gradeable calls, it decides whether to lean momentum or "
                   "pullback — on evidence, not a hunch. Too few yet to act on.)_")
    else:
        out.append("_🔬 Entry study (momentum vs pullback): accumulating — not enough closed bars yet._")
    out.append("")
    if closed and len(closed) < 20:
        out.append(f"_Only {len(closed)} closed — too few to conclude. Real money only after "
                   f"expectancy stays positive over ~20-30 calls._")
    out.append("_Auto-graded from real Upstox daily candles. No manual input. Same discipline "
               "that killed the intraday scanner — the numbers decide._")
    return "\n".join(out)


def main():
    text = report()
    print(text)
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if "--email" in flags:
        try:
            import bot
            bot.notify_email("Swing audit", text)
            print("[emailed]", file=sys.stderr)
        except Exception as e:
            print(f"[email failed: {e}]", file=sys.stderr)
    if "--ntfy" in flags and os.getenv("NTFY_TOPIC", "").strip():
        try:
            head = "\n".join(l for l in text.splitlines() if l.startswith("**"))
            urllib.request.urlopen(urllib.request.Request(
                "https://ntfy.sh/" + os.getenv("NTFY_TOPIC").strip(),
                data=(head or "swing audit").encode("utf-8"),
                headers={"Title": "Swing audit", "Tags": "clipboard"}), timeout=10)
            print("[pushed]", file=sys.stderr)
        except Exception as e:
            print(f"[ntfy failed: {e}]", file=sys.stderr)


if __name__ == "__main__":
    main()
