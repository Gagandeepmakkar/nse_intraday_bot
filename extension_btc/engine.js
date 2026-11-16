// Shared Donchian trend engine (no DOM) — used by both the popup and the background worker.
// Reads free Binance public data. Default = daily, long-only (the backtested edge). A faster
// interval + allowShort can be enabled for testing, but the backtest shows those LOSE after
// costs — the engine is honest about which mode is proven.
(function (root) {
  "use strict";
  async function klines(sym, interval, limit) {
    const r = await fetch(`https://api.binance.com/api/v3/klines?symbol=${sym}&interval=${interval}&limit=${limit}`);
    if (!r.ok) throw new Error("binance " + r.status);
    return (await r.json()).map(k => ({ t: k[0], o: +k[1], h: +k[2], l: +k[3], c: +k[4] }));
  }
  async function price(sym) {
    const r = await fetch(`https://api.binance.com/api/v3/ticker/price?symbol=${sym}`);
    return +(await r.json()).price;
  }
  function atr(c, i, p = 14) {
    let s = 0, n = 0;
    for (let k = Math.max(1, i - p + 1); k <= i; k++) { s += Math.max(c[k].h - c[k].l, Math.abs(c[k].h - c[k - 1].c), Math.abs(c[k].l - c[k - 1].c)); n++; }
    return n ? s / n : 0;
  }
  // Replay Donchian(period) long(+optional short) with a 3xATR chandelier trail on CLOSED
  // candles. Returns the CURRENT committed position + the levels to watch.
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
  // Full state for one symbol: closed-bar position + live price + P&L.
  async function state(sym, cfg) {
    const interval = cfg.interval || "1d", period = cfg.period || 20;
    const raw = await klines(sym, interval, period + 40);
    const closed = raw.slice(0, -1);                 // last candle is still forming
    const d = donchian(closed, period, !!cfg.allowShort);
    const px = await price(sym);
    const pnl = d.pos ? d.pos * (px / d.entry - 1) * 100 : 0;
    return { sym, interval, period, px, pnl, ...d };
  }
  root.BTCEngine = { klines, price, donchian, state };
})(typeof self !== "undefined" ? self : this);
