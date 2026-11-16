// Shared Donchian trend engine (no DOM) — used by the popup and the background worker.
// Pulls free crypto data with a FALLBACK CHAIN (Binance -> Coinbase) so it works whichever
// is reachable/CORS-ok in your browser/region. Long+short trend-following with an ATR trail.
(function (root) {
  "use strict";
  const SYM = { BTC: { binance: "BTCUSDT", cb: "BTC-USD" }, ETH: { binance: "ETHUSDT", cb: "ETH-USD" } };
  const IV = { "1d": { binance: "1d", cb: 86400 }, "4h": { binance: "4h", cb: 14400 }, "1h": { binance: "1h", cb: 3600 } };

  async function grab(url, parse) { const r = await fetch(url); if (!r.ok) throw new Error(url.split("/")[2] + " " + r.status); return parse(await r.json()); }

  // Normalised candles [{t,o,h,l,c}] ascending, whichever source answers first.
  async function klines(k, interval, limit) {
    const m = SYM[k], iv = IV[interval] || IV["1d"];
    try {
      return await grab(`https://api.binance.com/api/v3/klines?symbol=${m.binance}&interval=${iv.binance}&limit=${limit}`,
        j => j.map(c => ({ t: c[0], o: +c[1], h: +c[2], l: +c[3], c: +c[4] })));
    } catch (e) { /* fall through */ }
    // Coinbase returns newest-first [time,low,high,open,close,vol]; reverse to ascending.
    const arr = await grab(`https://api.exchange.coinbase.com/products/${m.cb}/candles?granularity=${iv.cb}`,
      j => j.map(c => ({ t: c[0] * 1000, l: +c[1], h: +c[2], o: +c[3], c: +c[4] })).reverse());
    return arr.slice(-limit);
  }
  async function price(k) {
    const m = SYM[k];
    try { return await grab(`https://api.binance.com/api/v3/ticker/price?symbol=${m.binance}`, j => +j.price); } catch (e) {}
    return await grab(`https://api.exchange.coinbase.com/products/${m.cb}/ticker`, j => +j.price);
  }
  function atr(c, i, p = 14) {
    let s = 0, n = 0;
    for (let k = Math.max(1, i - p + 1); k <= i; k++) { s += Math.max(c[k].h - c[k].l, Math.abs(c[k].h - c[k - 1].c), Math.abs(c[k].l - c[k - 1].c)); n++; }
    return n ? s / n : 0;
  }
  // Donchian(period) long+short with 3xATR chandelier trail, on CLOSED candles.
  function donchian(bars, period, allowShort) {
    let pos = 0, entry = 0, stop = 0, entryI = 0;
    for (let i = period + 1; i < bars.length; i++) {
      const c = bars[i].c, a = atr(bars, i);
      const hi = Math.max.apply(null, bars.slice(i - period, i).map(d => d.h));
      const lo = Math.min.apply(null, bars.slice(i - period, i).map(d => d.l));
      if (pos === 0) {
        if (c > hi) { pos = 1; entry = c; entryI = i; stop = Math.max.apply(null, bars.slice(i - period, i + 1).map(d => d.h)) - 3 * a; }
        else if (allowShort && c < lo) { pos = -1; entry = c; entryI = i; stop = Math.min.apply(null, bars.slice(i - period, i + 1).map(d => d.l)) + 3 * a; }
      } else if (pos === 1) {
        stop = Math.max(stop, Math.max.apply(null, bars.slice(i - period, i + 1).map(d => d.h)) - 3 * a);
        if (c < stop) pos = 0;
      } else {
        stop = Math.min(stop, Math.min.apply(null, bars.slice(i - period, i + 1).map(d => d.l)) + 3 * a);
        if (c > stop) pos = 0;
      }
    }
    const L = bars.length - 1;
    return {
      pos, entry, stop, entryBarsAgo: pos ? L - entryI : 0,
      upBreak: Math.max.apply(null, bars.slice(L - period + 1, L + 1).map(d => d.h)),
      dnBreak: Math.min.apply(null, bars.slice(L - period + 1, L + 1).map(d => d.l))
    };
  }
  async function state(k, cfg) {
    const interval = cfg.interval || "1d", period = cfg.period || 20;
    const closed = (await klines(k, interval, period + 45)).slice(0, -1);
    const d = donchian(closed, period, cfg.allowShort !== false);   // long+short by default
    const px = await price(k);
    const pnl = d.pos ? d.pos * (px / d.entry - 1) * 100 : 0;
    return { sym: k, interval, px, pnl, ...d };
  }
  root.BTCEngine = { klines, price, donchian, state };
})(typeof self !== "undefined" ? self : this);
