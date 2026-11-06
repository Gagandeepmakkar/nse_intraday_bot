# Setup — run on any device

NSE intraday research bot (scanner + catalyst-first + Time Machine odds + risk gate +
scheduler + email) plus TradingView Pine scripts and a Groww browser extension.

## 1. Clone
```bash
git clone https://github.com/Gagandeepmakkar/nse_intraday_bot.git
cd nse_intraday_bot
```

## 2. Create `.env`
`.env` is **not** in git (it holds your secrets). Either copy your existing `.env`
file over (AirDrop / USB — safest), or create it from the template below:

```bash
cp .env.example .env     # then edit .env and fill the two secrets
```

### `.env` template
Everything here is non-secret config **except the two `<...>` values**:
```
SOURCE=nse

# Capital & evidence-backed gates (validate with backtest.py before changing)
TRADING_CAPITAL_INR=100000
RISK_PER_TRADE_PCT=0.5
MAX_CANDIDATES=2
BUY_MAX=3
NEWS_SCAN_LIMIT=20
MIN_AVG_DAILY_TURNOVER_INR=50000000
MIN_RELATIVE_VOLUME=1.5
MIN_NET_REWARD_RISK=1.3
MIN_DAILY_RANGE_PCT=1.2
CANDLE_INTERVAL=5minute
LOOKBACK_DAYS=10
MAX_DEEP_SCAN=500
RVOL_MIN_SESSIONS=10
STALE_CANDLE_MINUTES=30
HISTORICAL_SLEEP_SEC=0.35
MARKET_TIMEZONE=Asia/Kolkata
RUN_TIMES_IST=09:12,10:15,13:15

# Dhan (free account) — token EXPIRES ~monthly; regenerate at web.dhan.co -> DhanHQ API
DHAN_CLIENT_ID=<YOUR_DHAN_CLIENT_ID>
DHAN_ACCESS_TOKEN=<YOUR_DHAN_TOKEN>

# Email alerts (Gmail SMTP + App Password from myaccount.google.com -> App passwords)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=<your_email@gmail.com>
SMTP_PASSWORD=<YOUR_GMAIL_APP_PASSWORD>
EMAIL_FROM=<your_email@gmail.com>
EMAIL_TO=<your_email@gmail.com>
```

### The only two secrets
| Secret | Where to get it | Expires? |
|---|---|---|
| `DHAN_ACCESS_TOKEN` | web.dhan.co → DhanHQ API → generate | Yes, ~monthly — regenerate when it stops working |
| `SMTP_PASSWORD` | Google account → Security → **App passwords** (needs 2FA on) | Only if revoked |

**Never** commit or share `.env` (it is gitignored).

## 3. Run
```bash
./run.sh            # scan now: scorecard + gainers + buy setups + top picks + dashboard
./run.sh auto       # scheduler: auto-run at the RUN_TIMES_IST on weekdays
./run.sh backtest   # profitability check, net of Indian costs
./run.sh track      # paper-trade scorecard of past picks
./run.sh serve      # localhost backend for the browser extension (port 8787)
```
First run auto-creates the Python venv and installs dependencies.

## 4. Per-device extras (not carried by `.env`)
- **Auto-start on login (macOS):** recreate the LaunchAgent that runs `scheduler.py`
  (`~/Library/LaunchAgents/com.nse.intraday.scheduler.plist`) with this machine's paths.
- **Groww extension:** `chrome://extensions` → Developer mode → **Load unpacked** →
  select the `extension/` folder. Open a Groww stock chart; hard-refresh after loading.
- **TradingView Pine:** paste a file from `tradingview/` (e.g. `nse_live.pine`) into the
  Pine Editor on TradingView.com.

## Honest note
This is personal research, not advice. The backtest/Time Machine show a weak edge —
**paper-trade first** and only risk money after your own results prove it. Most retail
intraday traders lose money.
