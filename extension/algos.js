/* Multi-algorithm intraday signal engine (pure functions, no DOM, no network).
 *
 * Input: candles = [{t, o, h, l, c, v}, ...] ascending by time (t = epoch sec).
 * Each algorithm returns { name, signal: 'BUY'|'SELL'|'NEUTRAL', note }.
 * decide() runs them all and returns a consensus with a live entry/stop/target.
 *
 * This is the "run several strategies on the live chart and agree" layer. It is
 * deliberately standard, well-known intraday logic — not a secret edge. Treat the
 * consensus as a confirmation checklist for the stock the bot already picked, and
 * execute manually.
 *
 * Works both in the browser (extension) and in Node (unit tests) — it exports via
 * module.exports when available and attaches to window.Algos in the browser.
 */
(function (root) {
  "use strict";

  function ema(values, span) {
    const k = 2 / (span + 1);
    let e = values[0];
    const out = [e];
    for (let i = 1; i < values.length; i++) {
      e = values[i] * k + e * (1 - k);
      out.push(e);
    }
    return out;
  }

  function rsi(closes, period = 14) {
    if (closes.length <= period) return 50;
    let gain = 0, loss = 0;
    for (let i = 1; i <= period; i++) {
      const d = closes[i] - closes[i - 1];
      if (d >= 0) gain += d; else loss -= d;
    }
    gain /= period; loss /= period;
    for (let i = period + 1; i < closes.length; i++) {
      const d = closes[i] - closes[i - 1];
      gain = (gain * (period - 1) + (d > 0 ? d : 0)) / period;
      loss = (loss * (period - 1) + (d < 0 ? -d : 0)) / period;
    }
    if (loss === 0) return 100;
    return 100 - 100 / (1 + gain / loss);
  }

  function atr(candles, period = 14) {
    if (candles.length < 2) return 0;
    const trs = [];
    for (let i = 1; i < candles.length; i++) {
      const h = candles[i].h, l = candles[i].l, pc = candles[i - 1].c;
      trs.push(Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc)));
    }
    const n = Math.min(period, trs.length);
    return trs.slice(-n).reduce((a, b) => a + b, 0) / n;
  }

  function sessionVwap(candles) {
    // VWAP over the latest calendar day present in the series.
    const lastDay = new Date(candles[candles.length - 1].t * 1000).getUTCDate();
    let pv = 0, vol = 0;
    for (const c of candles) {
      if (new Date(c.t * 1000).getUTCDate() !== lastDay) continue;
      const tp = (c.h + c.l + c.c) / 3;
      pv += tp * c.v; vol += c.v;
    }
    return vol > 0 ? pv / vol : candles[candles.length - 1].c;
  }

  // ---- Individual algorithms ---------------------------------------------- //
  function algoVwap(candles) {
    const price = candles[candles.length - 1].c;
    const vwap = sessionVwap(candles);
    const sig = price > vwap * 1.0005 ? "BUY" : price < vwap * 0.9995 ? "SELL" : "NEUTRAL";
    return { name: "VWAP", signal: sig, note: `price ${price.toFixed(2)} vs VWAP ${vwap.toFixed(2)}` };
  }

  function algoEmaCross(candles) {
    const closes = candles.map(c => c.c);
    if (closes.length < 21) return { name: "EMA 9/21", signal: "NEUTRAL", note: "not enough candles" };
    const e9 = ema(closes, 9), e21 = ema(closes, 21);
    const a = e9[e9.length - 1], b = e21[e21.length - 1];
    const sig = a > b ? "BUY" : a < b ? "SELL" : "NEUTRAL";
    return { name: "EMA 9/21", signal: sig, note: `EMA9 ${a.toFixed(2)} vs EMA21 ${b.toFixed(2)}` };
  }

  function algoOrb(candles, openCandles = 15) {
    // Opening-range breakout on the latest day (first `openCandles` 1-min bars).
    const lastDay = new Date(candles[candles.length - 1].t * 1000).getUTCDate();
    const today = candles.filter(c => new Date(c.t * 1000).getUTCDate() === lastDay);
    if (today.length < openCandles + 1) return { name: "ORB", signal: "NEUTRAL", note: "range forming" };
    const open = today.slice(0, openCandles);
    const hi = Math.max(...open.map(c => c.h)), lo = Math.min(...open.map(c => c.l));
    const price = today[today.length - 1].c;
    const sig = price > hi ? "BUY" : price < lo ? "SELL" : "NEUTRAL";
    return { name: "ORB", signal: sig, note: `range ${lo.toFixed(2)}-${hi.toFixed(2)}, price ${price.toFixed(2)}` };
  }

  function algoRsi(candles) {
    const r = rsi(candles.map(c => c.c));
    // Momentum reading: >55 favours longs, <45 favours shorts, extremes warn of reversal.
    const sig = r > 55 && r < 75 ? "BUY" : r < 45 && r > 25 ? "SELL" : "NEUTRAL";
    return { name: "RSI 14", signal: sig, note: `RSI ${r.toFixed(0)}` };
  }

  function algoSupertrend(candles, period = 10, mult = 3) {
    if (candles.length < period + 1) return { name: "Supertrend", signal: "NEUTRAL", note: "not enough candles" };
    const a = atr(candles, period);
    const last = candles[candles.length - 1];
    const mid = (last.h + last.l) / 2;
    const upper = mid + mult * a, lower = mid - mult * a;
    const sig = last.c > upper ? "BUY" : last.c < lower ? "SELL" : "NEUTRAL";
    return { name: "Supertrend", signal: sig, note: `bands ${lower.toFixed(2)}/${upper.toFixed(2)}` };
  }

  function algoVolumeSurge(candles, lookback = 20) {
    if (candles.length < lookback + 1) return { name: "Volume", signal: "NEUTRAL", note: "not enough candles" };
    const recent = candles.slice(-lookback - 1, -1);
    const avg = recent.reduce((s, c) => s + c.v, 0) / recent.length;
    const last = candles[candles.length - 1];
    const surge = avg > 0 ? last.v / avg : 0;
    if (surge < 1.5) return { name: "Volume", signal: "NEUTRAL", note: `${surge.toFixed(1)}x avg (quiet)` };
    // A surge confirms the direction of the last candle.
    const sig = last.c >= last.o ? "BUY" : "SELL";
    return { name: "Volume", signal: sig, note: `${surge.toFixed(1)}x avg surge` };
  }

  const ALGOS = [algoVwap, algoEmaCross, algoOrb, algoRsi, algoSupertrend, algoVolumeSurge];

  // ---- Consensus ----------------------------------------------------------- //
  function decide(candles, preferredSide) {
    if (!candles || candles.length < 22) {
      return { action: "WAIT", confidence: 0, votes: [], note: "need more live candles", levels: null };
    }
    const votes = ALGOS.map(fn => fn(candles));
    const buys = votes.filter(v => v.signal === "BUY").length;
    const sells = votes.filter(v => v.signal === "SELL").length;
    const net = buys - sells;
    const price = candles[candles.length - 1].c;
    const a = atr(candles) || price * 0.002;

    let action = "HOLD";
    if (net >= 2) action = "BUY";
    else if (net <= -2) action = "SELL";

    // If the bot picked a side, only fire when the live algos AGREE with it.
    if (preferredSide === "LONG" && action !== "BUY") action = "HOLD";
    if (preferredSide === "SHORT" && action !== "SELL") action = "HOLD";

    const confidence = Math.round((Math.abs(net) / ALGOS.length) * 100);
    let levels = null;
    if (action === "BUY") {
      levels = { entry: price, stop: price - 1.2 * a, target1: price + 1.5 * a, target2: price + 2.5 * a };
    } else if (action === "SELL") {
      levels = { entry: price, stop: price + 1.2 * a, target1: price - 1.5 * a, target2: price - 2.5 * a };
    }
    return { action, confidence, votes, net, price, atr: a, levels };
  }

  const api = { ema, rsi, atr, sessionVwap, decide, ALGOS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Algos = api;
})(typeof window !== "undefined" ? window : this);
