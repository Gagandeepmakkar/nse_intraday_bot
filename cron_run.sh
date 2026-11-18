#!/usr/bin/env bash
# Cron wrapper: run the scan, then notify. Called by crontab at the scheduled IST
# times. The bot's own market-closed guard makes weekend/holiday runs a no-op, and
# we only notify when FRESH picks were produced this run (not stale leftovers).
cd "$(dirname "$0")" || exit 1
LOG="cron.log"

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') ====="
  source .venv/bin/activate
  HM="$((10#$(TZ=Asia/Kolkata date +%H%M)))"
  # Keep the log/pick server up so the extension can record every signal + outcome to
  # journal.jsonl. Started once (persists via nohup); re-checked each slot.
  if ! curl -s -o /dev/null --max-time 2 http://127.0.0.1:8787/picks; then
    nohup python serve_picks.py >> serve.log 2>&1 &
    sleep 1
  fi
  python track.py || true          # grade yesterday's picks
  # Crypto is 24/7 — check BTC/ETH Donchian trend every slot; alert only on trend change.
  python crypto_monitor.py --ntfy --email || echo "crypto_monitor exited non-zero"
  # Pre-open WATCHLIST (morning, ~09:10): top 5 hot stocks for today with pre-open gaps.
  if [ "$HM" -ge 908 ] && [ "$HM" -lt 911 ]; then
    python watchlist.py --morning --ntfy --email || echo "watchlist morning failed"
    # SWING scan (the one brain): ranks the Nifty 500 and mails the best BUY/SELL
    # setups with entry/stop/target, or an honest SIT-OUT on extended days.
    python swing_scan.py --ntfy --email || echo "swing_scan failed"
  fi
  # Pre-market briefing (~09:12): deep read on your BRIEF_SYMBOLS.
  if [ "$HM" -ge 911 ] && [ "$HM" -lt 930 ]; then
    python briefing.py --ntfy --email || echo "briefing.py exited non-zero"
  fi
  python bot.py || echo "bot.py exited non-zero"
  # Evening WATCHLIST (~18:00): tomorrow's candidates from today's catalysts + movers.
  if [ "$HM" -ge 1800 ] && [ "$HM" -lt 1830 ]; then
    python watchlist.py --evening --ntfy --email || echo "watchlist evening failed"
  fi
  # During market hours: scan the WHOLE market for the day's top movers and push the
  # shortlist to phone — so you know which stocks to open in the extension.
  if [ "$HM" -ge 930 ] && [ "$HM" -le 1515 ]; then
    python movers.py --ntfy --email || echo "movers.py exited non-zero"
    # Hands-free trigger-watch: alert when a morning swing setup hits its buy zone,
    # target, or stop — so you don't have to stare at charts.
    python swing_watch.py --ntfy || echo "swing_watch.py exited non-zero"
  fi
  # After market close: self-audit the recorded signals/outcomes and send the report.
  if [ "$HM" -ge 1535 ] && [ "$HM" -lt 1600 ]; then
    python audit.py --ntfy --email || echo "audit.py exited non-zero"
    # Auto-grade every swing call against real prices (did it hit target/stop,
    # expected vs actual days) — the honesty layer for the swing engine.
    python swing_audit.py --ntfy --email || echo "swing_audit.py exited non-zero"
  fi
} >> "$LOG" 2>&1

# Pull the latest picks and only act if they were written in the last 10 minutes.
PICKS=$(python - <<'PY'
import sqlite3, datetime
try:
    con = sqlite3.connect("signals.db")
    row = con.execute("SELECT created_at FROM signals_v2 ORDER BY created_at DESC LIMIT 1").fetchone()
    if row:
        ts = datetime.datetime.strptime(row[0].replace(" IST", ""), "%Y-%m-%d %H:%M:%S")
        if (datetime.datetime.now() - ts).total_seconds() <= 600:  # fresh this run
            rows = con.execute(
                "SELECT symbol,side,entry,stop,target1 FROM signals_v2 WHERE created_at=?",
                (row[0],)).fetchall()
            print(" | ".join(f"{s} {sd} @{e:.1f} SL{st:.1f} T{t:.1f}" for s, sd, e, st, t in rows))
except Exception:
    pass
PY
)

if [ -n "$PICKS" ]; then
  osascript -e "display notification \"$PICKS\" with title \"NSE Intraday picks\" sound name \"Glass\"" 2>/dev/null || true
  open dashboard.html 2>/dev/null || true
  echo "notified: $PICKS" >> "$LOG"
else
  echo "no fresh picks (market closed or no qualified trade) — no notification" >> "$LOG"
fi
