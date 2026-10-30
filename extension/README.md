# NSE Intraday — Live Confirm (browser extension)

Stage 2 of the system. The Python bot picks the 2 best stocks; this extension runs
**multiple algorithms on their LIVE 1-minute charts** and gives a fresh
**BUY / SELL / HOLD + target** — computed now, not the bot's earlier prediction. It
only fires BUY/SELL when the live algos **agree with the side the bot chose**.

Execute every trade **manually** in your broker. This is a confirmation tool, not an
auto-trader and not advice.

## Two surfaces
**A) Groww overlay** — on any `groww.in` page, a floating draggable panel appears. It
auto-detects the stock you're viewing (when it's one of the bot's picks, else type the
symbol), runs the live algorithms, and shows BUY/SELL/HOLD + exact Entry/SL/T1/T2 with
a precise embedded chart. This is the "tell me where to buy on my chart" view.

**B) Popup** — click the toolbar icon for the same thing on the bot's 2 picks as tabs,
independent of what page you're on.

## What each shows
- A live candlestick chart (IST axis) with the live Entry/SL/T1/T2 drawn on it.
- A verdict: BUY / SELL / HOLD, with the % of algorithms that agree.
- Each algorithm's vote: VWAP, EMA 9/21, Opening-Range Breakout, RSI, Supertrend, Volume surge.
- It only says BUY/SELL when the live algos agree with the side the bot chose.

Note on data: the overlay gets the live price from the same free feed (Yahoo 1-min),
NOT by scraping Groww's pixels — more reliable. It reads only the *symbol* from the page.

## Load it (one time)
1. Start the publisher so the extension can read today's picks:
   ```
   ./run.sh serve        # serves http://127.0.0.1:8787/picks
   ```
   (Run the scan first — `./run.sh` — so there are picks to serve.)
2. Chrome → `chrome://extensions` → turn on **Developer mode** (top-right).
3. **Load unpacked** → select this `extension/` folder.
4. Click the puzzle icon → pin **NSE Intraday Live Confirm**, then click it to open.

If the publisher isn't running, the popup lets you type two symbols manually.

## The "Analyze this stock" button → deep Python backend
Pressing it does NOT just run the quick in-browser votes. It calls the local Python
backend (`/analyze` in serve_picks.py → analyze.py), which for that one stock:
- pulls 5-min + daily data and reads multi-timeframe trend + RSI/ATR/VWAP,
- runs a live mini-backtest of the breakout setup over the stock's recent sessions,
- returns a verdict WITH evidence (recent win-rate / expectancy), taking ~5-15s.

It HOLDs when the setup has no recent edge on that stock (negative expectancy) even if
the trend looks right — that veto is the point. **Requires the backend running:**
```
./run.sh serve      # http://127.0.0.1:8787  (/picks + /analyze)
```
If the backend is off, the panel says "backend offline — run ./run.sh serve".

## Data
Live 1-minute candles (for the chart) come free from Yahoo's chart API (lightly
delayed). The deep verdict comes from the Python backend. To upgrade to true tick
real-time later, swap the broker websocket into `data.js` / `background.js`.

## Files
- `algos.js` — the multi-algorithm engine (pure functions; unit-tested in Node).
- `data.js` — live candle + picks adapters.
- `popup.html/.css/.js` — the UI.
- `lib/lwc.js` — bundled TradingView lightweight-charts v4 (MV3 blocks remote scripts).
