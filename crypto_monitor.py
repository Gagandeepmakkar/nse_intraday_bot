"""24/7 crypto trend monitor — BTC & ETH Donchian trend-following, run from Python using
yfinance (works where exchange APIs like Binance are geo-blocked in India). Computes the
current LONG / SHORT / FLAT position with entry/stop, and pushes to your phone (ntfy) + email
ONLY when the trend changes (enter/exit) — low-frequency by design.

This is the one crypto approach that backtested positive over 8 years (see crypto_backtest.py):
Donchian-20 breakout + 3xATR chandelier trailing stop, long+short. Daily timeframe = the
proven edge. Not a prediction; honour the stop. No guarantees.

Run: python crypto_monitor.py                 (prints BTC/ETH trend state)
     python crypto_monitor.py --ntfy --email   (also alert on trend change)
Interval via --interval 1d|1h ; period via --period 20.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

STATE_FILE = "crypto_state.json"
SYMS = [("BTC-USD", "BTC"), ("ETH-USD", "ETH")]


def fetch(sym, interval):
    import yfinance as yf
    import pandas as pd
    period = "180d" if interval == "1d" else "60d"
    df = yf.download(sym, period=period, interval=interval, progress=False, auto_adjust=False)
    if df is None or df.empty:
        return []
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return [{"t": int(ts.timestamp()), "h": float(r["High"]), "l": float(r["Low"]), "c": float(r["Close"])}
            for ts, r in df.iterrows() if r["Close"] == r["Close"]]


def atr(b, i, p=14):
    s, n = 0.0, 0
    for k in range(max(1, i - p + 1), i + 1):
        s += max(b[k]["h"] - b[k]["l"], abs(b[k]["h"] - b[k - 1]["c"]), abs(b[k]["l"] - b[k - 1]["c"]))
        n += 1
    return s / n if n else 0.0


def donchian(bars, period, allow_short, mult=3.0):
    pos, entry, stop = 0, 0.0, 0.0
    for i in range(period + 1, len(bars)):
        c, a = bars[i]["c"], atr(bars, i)
        hi = max(x["h"] for x in bars[i - period:i])
        lo = min(x["l"] for x in bars[i - period:i])
        if pos == 0:
            if c > hi:
                pos, entry = 1, c; stop = max(x["h"] for x in bars[i - period:i + 1]) - mult * a
            elif allow_short and c < lo:
                pos, entry = -1, c; stop = min(x["l"] for x in bars[i - period:i + 1]) + mult * a
        elif pos == 1:
            stop = max(stop, max(x["h"] for x in bars[i - period:i + 1]) - mult * a)
            if c < stop:
                pos = 0
        else:
            stop = min(stop, min(x["l"] for x in bars[i - period:i + 1]) + mult * a)
            if c > stop:
                pos = 0
    L = len(bars) - 1
    up = max(x["h"] for x in bars[L - period + 1:L + 1])
    dn = min(x["l"] for x in bars[L - period + 1:L + 1])
    return pos, entry, stop, up, dn


def arg(flag, default):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def main():
    interval = arg("--interval", "1d")
    period = int(arg("--period", "20"))
    allow_short = "--long-only" not in sys.argv
    flags = {a for a in sys.argv if a.startswith("--")}
    try:
        old = json.load(open(STATE_FILE))
    except Exception:
        old = {}
    new_state, lines, changes = {}, [], []
    for sym, label in SYMS:
        bars = fetch(sym, interval)
        if len(bars) < period + 5:
            lines.append(f"{label}: no data"); continue
        closed = bars[:-1]
        pos, entry, stop, up, dn = donchian(closed, period, allow_short)
        px = bars[-1]["c"]
        name = "LONG" if pos == 1 else "SHORT" if pos == -1 else "FLAT"
        pnl = pos * (px / entry - 1) * 100 if pos else 0
        if pos:
            lines.append(f"{label} {name}  ${px:,.0f}  ({pnl:+.1f}%)  entry ${entry:,.0f} · stop ${stop:,.0f}")
        else:
            watch = f"long>${up:,.0f}" + (f" / short<${dn:,.0f}" if allow_short else "")
            lines.append(f"{label} FLAT  ${px:,.0f}  waiting — {watch}")
        prev = old.get(label)
        if prev is not None and prev != pos:
            if pos == 1: changes.append(f"🟢 {label}: breakout — ENTER LONG ~${px:,.0f}, stop ${stop:,.0f}")
            elif pos == -1: changes.append(f"🔴 {label}: breakdown — ENTER SHORT ~${px:,.0f}, stop ${stop:,.0f}")
            else: changes.append(f"⚪ {label}: trend over — EXIT (stop hit)")
        new_state[label] = pos
    json.dump(new_state, open(STATE_FILE, "w"))

    report = f"# ₿ Crypto trend ({interval} Donchian-{period}{' L/S' if allow_short else ' L'}) — {datetime.now():%d %b %H:%M}\n\n" + "\n".join(lines)
    print(report)
    if changes:
        print("\nTREND CHANGES:\n" + "\n".join(changes))
    if "--email" in flags and changes:
        try:
            import bot
            bot.notify_email("Crypto trend change: " + "; ".join(c.split(":")[0] for c in changes), report + "\n\n" + "\n".join(changes))
            print("[emailed]", file=sys.stderr)
        except Exception as e:
            print(f"[email failed: {e}]", file=sys.stderr)
    if "--ntfy" in flags and changes and os.getenv("NTFY_TOPIC", "").strip():
        try:
            urllib.request.urlopen(urllib.request.Request(
                "https://ntfy.sh/" + os.getenv("NTFY_TOPIC").strip(), data="\n".join(changes).encode("utf-8"),
                headers={"Title": "Crypto trend change", "Tags": "coin", "Priority": "high"}), timeout=10)
            print("[pushed]", file=sys.stderr)
        except Exception as e:
            print(f"[ntfy failed: {e}]", file=sys.stderr)


if __name__ == "__main__":
    main()
