// Shared Donchian trend engine (no DOM). Pulls free crypto OHLC from a FALLBACK CHAIN of
// exchanges — tries each until one works in your browser/region (Binance is geo-blocked in
// India, so CoinDCX/Bybit/Kraken are tried too). Long+short trend-following with ATR trail.
(function (root) {
  "use strict";
  // symbol + interval codes per source
  const S = {
    BTC: { bn: "BTCUSDT", by: "BTCUSDT", dcx: "B-BTC_USDT", kr: "XBTUSD", cb: "BTC-USD" },
    ETH: { bn: "ETHUSDT", by: "ETHUSDT", dcx: "B-ETH_USDT", kr: "ETHUSD", cb: "ETH-USD" }
  };
  const IV = {
    "1d": { bn: "1d", by: "D", dcx: "1d", kr: 1440, cb: 86400 },
    "4h": { bn: "4h", by: "240", dcx: "4h", kr: 240, cb: 14400 },
    "1h": { bn: "1h", by: "60", dcx: "1h", kr: 60, cb: 3600 }
  };
  async function j(url) { const r = await fetch(url); if (!r.ok) throw new Error(r.status); return r.json(); }
  const asc = (a) => a.sort((x, y) => x.t - y.t);

  // Each source: {name, candles(k,iv,limit)->[{t,o,h,l,c}] asc, price(k)->number}. India-friendly first.
  const SRC = [
    { name: "CoinDCX",
      candles: async (k, iv, n) => asc((await j(`https://public.coindcx.com/market_data/candles?pair=${S[k].dcx}&interval=${IV[iv].dcx}&limit=${n}`)).map(c => ({ t: c.time, o: +c.open, h: +c.high, l: +c.low, c: +c.close }))),
      price: async (k) => { const d = await j(`https://public.coindcx.com/market_data/candles?pair=${S[k].dcx}&interval=1m&limit=1`); return +d[0].close; } },
    { name: "Bybit",
      candles: async (k, iv, n) => asc(((await j(`https://api.bybit.com/v5/market/kline?category=spot&symbol=${S[k].by}&interval=${IV[iv].by}&limit=${n}`)).result.list).map(c => ({ t: +c[0], o: +c[1], h: +c[2], l: +c[3], c: +c[4] }))),
      price: async (k) => +((await j(`https://api.bybit.com/v5/market/tickers?category=spot&symbol=${S[k].by}`)).result.list[0].lastPrice) },
    { name: "Kraken",
      candles: async (k, iv, n) => { const r = (await j(`https://api.kraken.com/0/public/OHLC?pair=${S[k].kr}&interval=${IV[iv].kr}`)).result; const key = Object.keys(r).find(x => x !== "last"); return asc(r[key].map(c => ({ t: c[0] * 1000, o: +c[1], h: +c[2], l: +c[3], c: +c[4] }))).slice(-n); },
      price: async (k) => { const r = (await j(`https://api.kraken.com/0/public/Ticker?pair=${S[k].kr}`)).result; return +r[Object.keys(r)[0]].c[0]; } },
    { name: "Coinbase",
      candles: async (k, iv, n) => asc((await j(`https://api.exchange.coinbase.com/products/${S[k].cb}/candles?granularity=${IV[iv].cb}`)).map(c => ({ t: c[0] * 1000, l: +c[1], h: +c[2], o: +c[3], c: +c[4] }))).slice(-n),
      price: async (k) => +(await j(`https://api.exchange.coinbase.com/products/${S[k].cb}/ticker`)).price },
    { name: "Binance",
      candles: async (k, iv, n) => (await j(`https://api.binance.com/api/v3/klines?symbol=${S[k].bn}&interval=${IV[iv].bn}&limit=${n}`)).map(c => ({ t: c[0], o: +c[1], h: +c[2], l: +c[3], c: +c[4] })),
      price: async (k) => +(await j(`https://api.binance.com/api/v3/ticker/price?symbol=${S[k].bn}`)).price }
  ];

  async function fetchData(k, iv, n) {
    for (const s of SRC) {
      try { const candles = await s.candles(k, iv, n); const px = await s.price(k); if (candles.length > n * 0.6) return { candles, px, source: s.name }; } catch (e) { /* try next */ }
    }
    throw new Error("all crypto sources unreachable");
  }

  function atr(c, i, p = 14) { let s = 0, n = 0; for (let k = Math.max(1, i - p + 1); k <= i; k++) { s += Math.max(c[k].h - c[k].l, Math.abs(c[k].h - c[k - 1].c), Math.abs(c[k].l - c[k - 1].c)); n++; } return n ? s / n : 0; }
  function donchian(bars, period, allowShort) {
    let pos = 0, entry = 0, stop = 0, entryI = 0;
    for (let i = period + 1; i < bars.length; i++) {
      const c = bars[i].c, a = atr(bars, i);
      const hi = Math.max.apply(null, bars.slice(i - period, i).map(d => d.h));
      const lo = Math.min.apply(null, bars.slice(i - period, i).map(d => d.l));
      if (pos === 0) {
        if (c > hi) { pos = 1; entry = c; entryI = i; stop = Math.max.apply(null, bars.slice(i - period, i + 1).map(d => d.h)) - 3 * a; }
        else if (allowShort && c < lo) { pos = -1; entry = c; entryI = i; stop = Math.min.apply(null, bars.slice(i - period, i + 1).map(d => d.l)) + 3 * a; }
      } else if (pos === 1) { stop = Math.max(stop, Math.max.apply(null, bars.slice(i - period, i + 1).map(d => d.h)) - 3 * a); if (c < stop) pos = 0; }
      else { stop = Math.min(stop, Math.min.apply(null, bars.slice(i - period, i + 1).map(d => d.l)) + 3 * a); if (c > stop) pos = 0; }
    }
    const L = bars.length - 1;
    return { pos, entry, stop, entryBarsAgo: pos ? L - entryI : 0, upBreak: Math.max.apply(null, bars.slice(L - period + 1, L + 1).map(d => d.h)), dnBreak: Math.min.apply(null, bars.slice(L - period + 1, L + 1).map(d => d.l)) };
  }
  async function state(k, cfg) {
    const interval = cfg.interval || "1d", period = cfg.period || 20;
    const { candles, px, source } = await fetchData(k, interval, period + 45);
    const d = donchian(candles.slice(0, -1), period, cfg.allowShort !== false);
    const pnl = d.pos ? d.pos * (px / d.entry - 1) * 100 : 0;
    return { sym: k, interval, px, pnl, source, ...d };
  }
  root.BTCEngine = { fetchData, donchian, state };
})(typeof self !== "undefined" ? self : this);
