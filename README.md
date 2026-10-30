# NSE Intraday Research Engine (notification-only)

Private Python service for NSE cash-equity research using Zerodha Kite Connect. It discovers and ranks up to `MAX_CANDIDATES` intraday setups across the **liquid NSE universe**, computes **net reward:risk after Indian transaction costs**, and labels each setup **CONFIRMED / PENDING / UNVERIFIED**. It sends the ranked report to Telegram and email. It has **no order-placement code** and no dashboard.

**Personal research use only — not investment advice and not for redistribution.** Distributing stock-specific buy/sell calls to others in India may require SEBI Research Analyst registration.

## How it works

1. **Broad discovery (cheap):** bulk-quotes *all* NSE EQ instruments for turnover, day-change, breadth and order-book spread. This layer covers the whole liquid universe.
2. **Deep scan (rate-limited):** pulls 5-minute candles for the top-`MAX_DEEP_SCAN` liquid names and runs a layered screen — time-adjusted RVOL, VWAP, EMA20/50 trend, ATR stop, opening range, prev-day levels, CPR/Camarilla pivots.
3. **Risk & costs:** sizes by risk budget, builds 2R/3R targets, then subtracts a configurable Indian intraday cost model (brokerage, STT, exchange, SEBI, stamp, GST, slippage) to report **net** R:R.
4. **Ranking:** a documented composite score (relative strength, RVOL, structure, liquidity, risk-feasibility, order-flow, catalyst) ranks survivors; it returns up to `MAX_CANDIDATES`, or a valid NO-TRADE.

Modules: `bot.py` (orchestrator), `indicators.py` (technical functions), `costs.py` (cost model), `datasource.py` (Kite + offline mock), `auth.py` (login).

## Important limitations

- This is a research tool, not a validated profitable strategy or financial advice.
- It evaluates completed 5-minute candles and may be too slow or incomplete for fast intraday execution.
- **Coverage is honest, not total:** the deep (candle) scan is capped at `MAX_DEEP_SCAN` because Kite historical calls are rate-limited. The report states how many instruments were available, liquid, scanned and qualified.
- **Not available in this build:** derivatives OI, intraday FII/DII flows, and historically validated expectancy (reported as UNVALIDATED). BSE is not scanned (NSE only).
- Position sizing excludes exchange interruptions and gap risk, and cannot guarantee a maximum loss.
- The symbols list is only a fallback seed; the engine builds its universe live from Kite instruments.
- The optional AI summary only summarizes headlines you put into `news.csv`. This package does not scrape arbitrary news sites or include a paid news feed. Use only news sources/APIs whose terms permit your use.
- This version sends one scan report when run; schedule it using cron/Task Scheduler. It does not yet stream ticks continuously or monitor live stop/target hits.
- Validate the signals in paper/shadow mode before trading real money. Backtest with costs, slippage and realistic fills.

## 1. Install

Python 3.11+ recommended.

### macOS/Linux
```bash
mkdir nse-intraday-bot && cd nse-intraday-bot
# Extract this zip here, then:
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Windows PowerShell
```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` locally. Never send it to anyone or commit it.

## 2. Configure Kite Connect

1. Create/verify your Kite Connect app in the official developer console.
2. Put your `KITE_API_KEY` and `KITE_API_SECRET` in `.env`.
3. Run `python auth.py`.
4. Open the printed Kite login URL, log in, then copy the `request_token` query parameter from the registered redirect URL.
5. Paste it into the terminal prompt. Input is hidden. The resulting daily `access_token` is stored in `token.json`.
6. Keep `token.json` private; it is excluded from Git. On Unix/macOS it is chmod 600. The token expires daily, so repeat login after expiry.

Never place the API secret or access token in a browser app, a Gem, a source file, Telegram, or email. Use the redirect URL configured in the Kite developer console exactly as required by your app setup. If using a local redirect, ensure the callback host/port and the registered URL match your configuration; this starter's login flow uses manual request-token copy/paste, so it does not require a local callback server.

Official docs:
- https://kite.trade/docs/connect/v3/user/
- https://kite.trade/docs/connect/v3/historical/
- https://kite.trade/docs/connect/v3/websocket/

## 3. Telegram

1. In Telegram, message `@BotFather`, create a bot, and copy its token.
2. Start a private conversation with the bot.
3. Find your numeric chat ID using a trusted Telegram bot-update method or your own `getUpdates` call after messaging your bot.
4. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.
5. Never share the bot token; anyone who has it can control the bot.

## 4. Email

For Gmail, enable 2-Step Verification and create an app password; use that in `SMTP_PASSWORD`. Do not use your normal account password. For another provider, update SMTP host/port and use its app-password or SMTP credential process. Use TLS on port 587.

## 5. Configure the stock universe and risk

- Edit `symbols.txt`; one NSE equity trading symbol per line.
- Set `TRADING_CAPITAL_INR` to your actual trading capital.
- `RISK_PER_TRADE_PCT=0.5` makes planned risk budget = capital × 0.005.
- Quantity is capped by both that risk budget and the configured capital, but does not include brokerage/taxes/slippage and cannot guarantee a maximum loss.
- Set `MIN_RELATIVE_VOLUME`, `MIN_REWARD_RISK`, `MAX_CANDIDATES`, and the other filters conservatively.
- `news.csv` can contain columns `symbol,published_at,source,headline,url`. Use permitted sources only. Without `OPENAI_API_KEY`, the bot will not call the AI API.

## 6. Test and run

Verify the full pipeline **offline** first — no credentials or market hours required. This uses deterministic synthetic data (illustrative numbers only):
```bash
python bot.py --offline
```

Live run — choose a data source:
```bash
python bot.py --source dhan    # DhanHQ: free historical data (recommended)
python bot.py --source kite    # Zerodha Kite (needs paid historical add-on)
python bot.py                  # uses SOURCE in .env (default)
```

**DhanHQ setup:** generate an access token in the Dhan web dashboard (DhanHQ API section), then set `DHAN_CLIENT_ID` and `DHAN_ACCESS_TOKEN` in `.env`. Symbols are resolved from Dhan's scrip-master CSV automatically. Historical candles are free; live quotes/market-depth may require Dhan's Data API plan. The Dhan adapter is not yet verified against the live API — on the first run, check that candle times look correct (09:15–15:30 IST); if they are shifted by 5h30m, flip the timestamp conversion noted in `datasource.py`.

This prints a ranked report, sends notifications if configured, and records recommendations in `signals.db` (table `signals_v2`).

Run a dry configuration check by leaving Telegram/email settings blank; the report prints locally and logs that notifications are skipped.

## 7. Schedule it

Run this at the desired times using cron, systemd timers, Windows Task Scheduler, or a cloud scheduler. Example Linux crontab (adjust the project path and local timezone):

```cron
# Morning pre-market scan at 08:45 IST, weekdays
45 8 * * 1-5 cd /opt/nse-intraday-bot && .venv/bin/python bot.py >> bot.log 2>&1
# Opening-session scan at 09:20 IST, weekdays
20 9 * * 1-5 cd /opt/nse-intraday-bot && .venv/bin/python bot.py >> bot.log 2>&1
# Refresh every 15 minutes during session (weekdays); tune as needed
*/15 9-14 * * 1-5 cd /opt/nse-intraday-bot && .venv/bin/python bot.py >> bot.log 2>&1
```

Cron timezone is often the server timezone, not necessarily IST. Set the server/cron timezone explicitly or use a scheduler that supports `Asia/Kolkata`. These examples do not exclude exchange holidays; the bot may send a no-data report on holidays. Also, the access token expires daily: run `python auth.py` after login expiry before the scheduled scans.

## 8. What to improve before relying on it

1. Add a permitted automated news provider and freshness filters.
2. Add a daily instrument/eligibility refresh and a better-defined liquidity universe.
3. Use time-of-day-adjusted relative volume and session VWAP.
4. Add a proper live WebSocket listener, data-staleness checks, market-holiday calendar, and signal deduplication.
5. Add backtesting and out-of-sample validation with transaction costs.
6. Keep all execution manual. Review every recommendation before acting.

## Troubleshooting

- `TokenException`: repeat `python auth.py`.
- Empty scan: check token, internet, market session, symbols, and configured filters.
- No notifications: verify Telegram chat ID/token and SMTP credentials.
- `news.csv` not available: the price/volume scanner still works; AI news notes are unavailable unless headlines and an AI API key are configured.
