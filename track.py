"""Paper-trade tracker — grade past picks with free data, show a running scorecard.

No money involved. This is how you learn whether the bot's picks actually work
BEFORE risking anything. It reads the signals the bot saved to signals.db, checks
what each stock actually did after the signal (hit target or stop first), and
tallies a cost-adjusted scorecard over time.

Grade a pick only once its session has data to grade against, so run this a day
(or more) AFTER generating signals — e.g. each morning before the new scan. Free
intraday history goes back ~60 days, so older signals can't be graded.

Run: python track.py
"""
import sqlite3

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

import backtest  # reuse simulate_fill

load_dotenv()
DB = "signals.db"


def _ensure(con):
    con.execute("""CREATE TABLE IF NOT EXISTS outcomes(
        created_at TEXT, symbol TEXT, side TEXT, result TEXT, net REAL,
        PRIMARY KEY(created_at, symbol))""")


def grade(con):
    """Grade every ungraded past signal that now has post-signal candles."""
    _ensure(con)
    done = {(a, s) for a, s in con.execute("SELECT created_at, symbol FROM outcomes")}
    sigs = con.execute("SELECT created_at,symbol,side,entry,stop,target1,target2,qty FROM signals_v2").fetchall()
    cache, new = {}, 0
    for created_at, sym, side, entry, stop, t1, t2, qty in sigs:
        if (created_at, sym) in done:
            continue
        dt = pd.to_datetime(created_at.replace(" IST", ""), errors="coerce")
        if pd.isna(dt):
            continue
        if sym not in cache:
            try:
                h = yf.Ticker(f"{sym}.NS").history(period="60d", interval="5m")
                if h.index.tz is not None:
                    h.index = h.index.tz_convert("Asia/Kolkata").tz_localize(None)
                cache[sym] = h
            except Exception:
                cache[sym] = None
        h = cache[sym]
        if h is None or h.empty:
            continue
        same = h[h.index.date == dt.date()]
        ev = same[same.index > dt]  # candles after the signal time, same session
        if ev.empty:
            continue  # after-hours signal or session not yet available — grade later
        rows = [{"high": float(c.High), "low": float(c.Low), "close": float(c.Close)}
                for c in ev.itertuples()]
        result, net = backtest.simulate_fill(side, entry, stop, t1, t2, qty, rows)
        con.execute("INSERT OR IGNORE INTO outcomes VALUES(?,?,?,?,?)",
                    (created_at, sym, side, result, net))
        new += 1
    con.commit()
    return new


def scorecard(con):
    _ensure(con)
    all_rows = con.execute("SELECT result, net FROM outcomes").fetchall()
    filled = [(r, n) for r, n in all_rows if r != "NOFILL"]
    nofill = len(all_rows) - len(filled)
    print("\n" + "=" * 56)
    print("PAPER-TRADE SCORECARD  (free data, optimistic fills, no money)")
    print("=" * 56)
    if not filled:
        graded_note = (f"{len(all_rows)} signal(s) seen, none filled yet "
                       if all_rows else "No graded signals yet ")
        print(graded_note + "— run the bot during market hours, then run this the")
        print("next day so there are post-signal candles to grade against.")
        print("=" * 56)
        return
    wins = [n for _, n in filled if n > 0]
    losses = [n for _, n in filled if n <= 0]
    total = sum(n for _, n in filled)
    gp, gl = sum(wins), -sum(losses)
    pf = gp / gl if gl > 0 else float("inf")
    n = len(filled)
    print(f"Picks graded:   {len(all_rows)}  (filled {n}, never-triggered {nofill})")
    print(f"Win rate:       {len(wins) / n * 100:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"Paper P&L:      Rs {total:,.0f}  (after Indian costs + slippage)")
    print(f"Profit factor:  {pf:.2f}   (>1 = the picks make money)")
    print(f"Expectancy:     Rs {total / n:,.0f} per filled pick")
    print("-" * 56)
    if n < 20:
        print(f"Only {n} filled picks — too few to trust. Keep paper-trading.")
    elif total > 0 and pf >= 1.3:
        print("Positive so far — promising, but keep going; then start TINY.")
    else:
        print("Not positive yet — do NOT risk money. Keep observing / refining.")
    print("=" * 56)


if __name__ == "__main__":
    con = sqlite3.connect(DB)
    added = grade(con)
    if added:
        print(f"Graded {added} new pick(s).")
    scorecard(con)
    con.close()
