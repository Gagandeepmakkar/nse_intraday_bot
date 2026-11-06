# TradingView Pine script — `nse_intraday.pine`

A chart indicator that mirrors the Python bot's logic so your chart agrees with your
emails: session VWAP + ±1σ/±2σ bands, EMA20/50, opening-range (ORB) high/low, RSI,
daily-timeframe trend, and LONG/SHORT setup markers with entry/stop/T1/T2 drawn on the
chart. Confirmation only — trade manually.

## Where it runs
- ✅ **TradingView.com** (your own account) — custom scripts only run here.
- ❌ **Groww** — Groww's chart cannot run custom Pine scripts (only TradingView's
  built-in indicators). On Groww, add **VWAP + EMA(20) + EMA(50) + RSI(14)** from the
  built-in indicator list to replicate most of this for free, in real time.
- Data note: TradingView's free NSE data is ~15-min delayed (same as the bot). Real-time
  needs a paid TradingView NSE data plan. Groww's own chart is real-time but no scripts.

## Add it to a chart
1. TradingView.com → open a **5-minute** NSE chart (e.g. `NSE:RELIANCE`).
2. Bottom panel → **Pine Editor** → paste the contents of `nse_intraday.pine`.
3. Click **Add to chart**. Save it (name it "NSE Intraday").
4. Optional: right-click a LONG/SHORT alert → **Add alert** → set a **Webhook URL** to
   send triggers back to the bot later.

## How it fits the system
- The **Python bot** scans 500 stocks and names the 2 best (email/dashboard).
- Open those 2 on TradingView with this script to **see** the live VWAP/EMA/ORB/RSI and
  the exact entry/stop/target on the chart before you act.
- Same setup logic both places, so the chart and the emails agree.

## Inputs you can tune
Opening-range minutes (15), EMA fast/slow (20/50), RSI length (14), ATR length (14),
and the stop/T1/T2 ATR multiples (1.2 / 1.5 / 2.5) — matching the bot's defaults.
