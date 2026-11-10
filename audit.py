"""Self-audit — read journal.jsonl (every signal + graded outcome the extension recorded)
and tell you, from REAL recorded results, whether the system is working and where it's
leaking money.

Principle: this is measurement + self-correction, NEVER prediction. Every number is
computed deterministically from recorded trade outcomes. AI (if a key is set) is used
ONLY to rewrite the computed findings as a readable summary — it invents nothing.

Run: python audit.py              (prints the report)
     python audit.py --email --ntfy   (also send it)
The scheduler runs it automatically after market close when a post-15:30 slot exists.
"""
import json
import os
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

JOURNAL = "journal.jsonl"


def load():
    rows = []
    try:
        with open(JOURNAL, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return rows


def stats(closes):
    n = len(closes)
    if not n:
        return None
    rs = [c.get("R", 0) for c in closes]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gw, gl = sum(wins), -sum(losses)
    run = peak = mdd = 0.0
    for r in rs:
        run += r
        peak = max(peak, run)
        mdd = min(mdd, run - peak)
    return dict(n=n, wr=len(wins) / n, tot=sum(rs), exp=sum(rs) / n,
                aw=(gw / len(wins) if wins else 0), al=(gl / len(losses) if losses else 0),
                pf=(gw / gl if gl > 0 else float("inf") if gw > 0 else 0),
                mdd=mdd, wins=len(wins), losses=len(losses))


def by(closes, keyfn):
    g = defaultdict(list)
    for c in closes:
        g[keyfn(c)].append(c)
    return {k: stats(v) for k, v in g.items()}


def flags(closes, signals):
    out = []
    s = stats(closes)
    if not s:
        return ["No closed trades recorded yet — nothing to audit. Keep paper-trading; "
                "make sure serve_picks.py is running so the extension can log outcomes."]
    unresolved = len(signals) - len(closes)
    if unresolved > 0:
        out.append(f"{unresolved} signal(s) fired without a recorded outcome (still open or not logged).")
    if s["n"] < 15:
        out.append(f"Only {s['n']} closed trades — TOO FEW to conclude anything. Need ~20–30.")
    if s["exp"] <= 0:
        out.append(f"Expectancy {s['exp']:+.2f}R over {s['n']} trades → NO edge yet. Do NOT risk real money.")
    else:
        out.append(f"Expectancy {s['exp']:+.2f}R, profit factor {s['pf']:.2f} over {s['n']} trades → tentatively positive — keep validating.")
    exits = Counter(c.get("exit") for c in closes)
    sl = exits.get("SL", 0)
    if s["n"] and sl / s["n"] > 0.5:
        out.append(f"{sl}/{s['n']} trades full-stopped before T1 → entries weak or stop too tight (review Stop ATR / entry filter).")
    for side, ss in by(closes, lambda c: c.get("side")).items():
        if ss and ss["n"] >= 8 and ss["exp"] < 0:
            out.append(f"{side} trades are net-negative (exp {ss['exp']:+.2f}R, n={ss['n']}) → consider disabling {side}.")
    for hr, hs in sorted(by(closes, lambda c: datetime.fromtimestamp(c.get("t", 0) / 1000).strftime("%H:00")).items()):
        if hs and hs["n"] >= 6 and hs["exp"] < -0.2:
            out.append(f"{hr} window is net-negative (exp {hs['exp']:+.2f}R, n={hs['n']}) → avoid trading that hour.")
    for st, ts in by(closes, lambda c: c.get("strat", "?")).items():
        if ts and ts["n"] >= 8:
            out.append(f"Strategy '{st}': exp {ts['exp']:+.2f}R, win {ts['wr']*100:.0f}%, n={ts['n']}.")
    return out


def ai_summary(findings):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key or not findings:
        return None
    model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    prompt = ("You are a trading-system QA analyst. Below are COMPUTED findings (facts) from "
              "recorded trade outcomes. In 4-5 lines, summarize what is working, what is leaking "
              "money, and which SETTINGS to review (e.g. disable a side, avoid an hour, widen the "
              "stop). Rules: do NOT predict markets, do NOT invent numbers — only restate and "
              "prioritize the findings given.\nFindings:\n- " + "\n- ".join(findings))
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"  (AI summary unavailable: {e})", file=sys.stderr)
        return None


def main():
    rows = load()
    closes = [r for r in rows if r.get("event") == "close"]
    signals = [r for r in rows if r.get("event") == "signal"]
    s = stats(closes)

    out = [f"# 🔍 System audit — {datetime.now():%d %b %Y, %H:%M}",
           f"Recorded: {len(signals)} signals, {len(closes)} closed trades.\n"]
    if s:
        out.append(f"**Overall:** {s['tot']:+.2f}R total · exp {s['exp']:+.2f}R/trade · "
                   f"win {s['wr']*100:.0f}% ({s['wins']}/{s['losses']}) · PF {s['pf']:.2f} · maxDD {s['mdd']:.1f}R")
    out.append("")
    fl = flags(closes, signals)
    out.append("**Findings (computed from real outcomes):**")
    out += [f"- {f}" for f in fl]

    summary = ai_summary(fl)
    if summary:
        out.append("")
        out.append("**🤖 AI summary of the findings (restates the facts — no prediction):**")
        out.append(summary)

    out.append("\n---\n_Everything above is computed from recorded trade outcomes. Use it to tune "
               "settings deterministically; never trade a fixed daily target. Real money only after "
               "expectancy stays positive over a real sample._")
    report = "\n".join(out)
    print(report)

    flagset = {a for a in sys.argv[1:] if a.startswith("--")}
    if "--email" in flagset:
        try:
            import bot
            bot.notify_email("System audit", report)
            print("\n[emailed]", file=sys.stderr)
        except Exception as e:
            print(f"\n[email failed: {e}]", file=sys.stderr)
    if "--ntfy" in flagset and os.getenv("NTFY_TOPIC", "").strip():
        try:
            head = "\n".join(l for l in out if l.startswith("**Overall") or l.startswith("- Expectancy") or l.startswith("- No "))
            urllib.request.urlopen(urllib.request.Request(
                "https://ntfy.sh/" + os.getenv("NTFY_TOPIC").strip(), data=(head or "audit ready").encode(),
                headers={"Title": "System audit", "Priority": "default"}), timeout=10)
            print("[pushed]", file=sys.stderr)
        except Exception as e:
            print(f"[ntfy failed: {e}]", file=sys.stderr)


if __name__ == "__main__":
    main()
