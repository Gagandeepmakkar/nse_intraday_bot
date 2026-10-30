#!/usr/bin/env bash
# One command to run the NSE intraday bot. All market data is live from the
# internet (Yahoo for prices/candles/indices, Google News for headlines) — no mock.
#   ./run.sh            -> EVERYTHING: paper-trade scorecard + gainers + buy setups + top picks
#   ./run.sh buy        -> only the day-gainers with a LONG setup (entry/SL/target)
#   ./run.sh gainers    -> only the raw list of stocks up on the day
#   ./run.sh track      -> only the paper-trade scorecard (how past picks did)
#   ./run.sh backtest   -> profitability check, net of costs
#   ./run.sh auto       -> scheduler: auto-run at pre-open + decision times (skips holidays)
#   ./run.sh serve      -> localhost publisher feeding the browser extension (port 8787)
# First run auto-creates the virtual environment and installs dependencies.
set -e
cd "$(dirname "$0")"

# Bootstrap venv + deps on first run (or if yfinance is missing).
if [ ! -d .venv ]; then
  echo "First run: creating virtual environment..."
  python3 -m venv .venv
fi
source .venv/bin/activate
if ! python -c "import yfinance" 2>/dev/null; then
  echo "Installing dependencies..."
  pip install -q -r requirements.txt
fi

# Dispatch: "backtest" runs the backtest, "gainers" lists the day's gainers;
# anything else passes through to the bot.
if [ "$1" = "backtest" ]; then
  shift
  exec python backtest.py "$@"
elif [ "$1" = "gainers" ]; then
  exec python bot.py --gainers
elif [ "$1" = "buy" ]; then
  exec python bot.py --buy
elif [ "$1" = "track" ]; then
  exec python track.py
elif [ "$1" = "auto" ]; then
  exec python scheduler.py
elif [ "$1" = "serve" ]; then
  exec python serve_picks.py
else
  # Default: grade how past picks did (scorecard), then run today's full scan.
  python track.py || true
  exec python bot.py "$@"
fi
