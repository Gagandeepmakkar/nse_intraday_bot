/* Live data adapters for the extension (browser only).
 *
 * fetchCandles(symbol): 1-minute OHLCV for the session from Yahoo's chart API.
 *   Free and CORS is bypassed because the extension declares host_permissions for
 *   query*.finance.yahoo.com. Intraday 1m is lightly delayed but updates through the
 *   day — enough to run the live algorithms on. Swap this one function for your
 *   broker's websocket later for true tick real-time; nothing else changes.
 *
 * fetchPicks(): the bot's current 2 picks from the local publisher (serve_picks.py).
 *   Falls back to null so the popup can ask you to type symbols manually.
 */
window.Data = (function () {
  "use strict";

  async function fetchCandles(symbol) {
    const base = "https://query1.finance.yahoo.com/v8/finance/chart/";
    const url = `${base}${encodeURIComponent(symbol)}.NS?interval=1m&range=1d`;
    const r = await fetch(url);
    if (!r.ok) throw new Error(`Yahoo ${r.status}`);
    const j = await r.json();
    const res = j.chart && j.chart.result && j.chart.result[0];
    if (!res || !res.timestamp) return [];
    const ts = res.timestamp, q = res.indicators.quote[0];
    const out = [];
    for (let i = 0; i < ts.length; i++) {
      if (q.close[i] == null) continue;  // skip the still-forming / empty candle
      out.push({ t: ts[i], o: q.open[i], h: q.high[i], l: q.low[i], c: q.close[i], v: q.volume[i] || 0 });
    }
    return out;
  }

  async function fetchPicks() {
    try {
      const r = await fetch("http://127.0.0.1:8787/picks", { cache: "no-store" });
      if (!r.ok) return null;
      return await r.json();  // [{symbol, side, entry, stop, target1, target2, label, score}, ...]
    } catch (e) {
      return null;
    }
  }

  return { fetchCandles, fetchPicks };
})();
