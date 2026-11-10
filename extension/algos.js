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

  // Supertrend direction series (1 = bullish/line below price, -1 = bearish).
  function supertrend(candles, period, mult) {
    const n = candles.length;
    const dir = new Array(n).fill(0);
    const line = new Array(n).fill(null);
    if (n < period + 2) return { dir, line };
    const tr = [0];
    for (let i = 1; i < n; i++) {
      const h = candles[i].h, l = candles[i].l, pc = candles[i - 1].c;
      tr.push(Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc)));
    }
    let a = 0;
    for (let i = 1; i <= period; i++) a += tr[i];
    a /= period;
    let finalUp = 0, finalLo = 0, prevST = 0;
    for (let i = period; i < n; i++) {
      if (i > period) a = (a * (period - 1) + tr[i]) / period;
      const hl2 = (candles[i].h + candles[i].l) / 2;
      const bu = hl2 + mult * a, bl = hl2 - mult * a;
      const pfu = i > period ? finalUp : bu;
      const pfl = i > period ? finalLo : bl;
      finalUp = (bu < pfu || candles[i - 1].c > pfu) ? bu : pfu;
      finalLo = (bl > pfl || candles[i - 1].c < pfl) ? bl : pfl;
      let d;
      if (i === period) d = candles[i].c <= finalUp ? -1 : 1;
      else if (prevST === pfu) d = candles[i].c > finalUp ? 1 : -1;
      else d = candles[i].c < finalLo ? -1 : 1;
      dir[i] = d;
      prevST = d === 1 ? finalLo : finalUp;
      line[i] = prevST;
    }
    return { dir, line };
  }

  // ADX + directional indicators (Wilder) — trend-strength filter.
  function adxDMI(candles, len) {
    const n = candles.length;
    if (n < len * 2) return { adx: 0, plusDI: 0, minusDI: 0 };
    const tr = [], pDM = [], mDM = [];
    for (let i = 1; i < n; i++) {
      const up = candles[i].h - candles[i - 1].h, dn = candles[i - 1].l - candles[i].l;
      pDM.push(up > dn && up > 0 ? up : 0);
      mDM.push(dn > up && dn > 0 ? dn : 0);
      tr.push(Math.max(candles[i].h - candles[i].l, Math.abs(candles[i].h - candles[i - 1].c), Math.abs(candles[i].l - candles[i - 1].c)));
    }
    const wilder = (arr) => { let s = arr.slice(0, len).reduce((a, b) => a + b, 0); const out = [s]; for (let i = len; i < arr.length; i++) { s = s - s / len + arr[i]; out.push(s); } return out; };
    const sTR = wilder(tr), sP = wilder(pDM), sM = wilder(mDM);
    const dx = [];
    for (let i = 0; i < sTR.length; i++) { const t = sTR[i] || 1; const pdi = 100 * sP[i] / t, mdi = 100 * sM[i] / t; dx.push(100 * Math.abs(pdi - mdi) / ((pdi + mdi) || 1)); }
    let adx = dx.slice(0, len).reduce((a, b) => a + b, 0) / len;
    for (let i = len; i < dx.length; i++) adx = (adx * (len - 1) + dx[i]) / len;
    const j = sTR.length - 1, t = sTR[j] || 1;
    return { adx, plusDI: 100 * sP[j] / t, minusDI: 100 * sM[j] / t };
  }

  function macdHistAt(closes) {
    const e12 = ema(closes, 12), e26 = ema(closes, 26);
    const macd = e12.map((v, k) => v - e26[k]);
    const sig = ema(macd, 9);
    const i = closes.length - 1;
    return { hist: macd[i] - sig[i], prev: macd[i - 1] - sig[i - 1] };
  }

  // Higher-timeframe trend from a candle series (EMA9 vs EMA21): "UP"/"DOWN"/"FLAT".
  function htfTrendOf(candles) {
    if (!candles || candles.length < 22) return "FLAT";
    const c = candles.map(x => x.c);
    const e9 = ema(c, 9), e21 = ema(c, 21), i = c.length - 1;
    return e9[i] > e21[i] ? "UP" : e9[i] < e21[i] ? "DOWN" : "FLAT";
  }

  // Full-parity scored signal with nse_live.pine: 0-100 confluence score
  // (HTF 15 + EMA-stack 15 + VWAP 15 + ADX/DI 15 + RVOL 10 + RSI 10 + MACD 10 + trigger 10),
  // a STRUCTURAL stop = min(Supertrend, 5-bar swing) validated to 1-2.5 ATR, and 1.5R/3R
  // targets. opts = { minScore, htfTrend ("UP"/"DOWN"/"FLAT") }.
  function swingDecide(candles, opts) {
    opts = opts || {};
    const minScore = opts.minScore || 70;
    const htf = opts.htfTrend || "FLAT";
    if (!candles || candles.length < 55) return { action: "WAIT", note: "loading candles…", score: 0, trend: "?", htf };
    const closes = candles.map(c => c.c);
    const e20 = ema(closes, 20), e50 = ema(closes, 50), e200 = ema(closes, 200);
    const i = closes.length - 1, price = closes[i];
    const a = atr(candles) || price * 0.003;
    const r = rsi(closes) || 50;
    const vw = sessionVwap(candles);
    const { adx, plusDI, minusDI } = adxDMI(candles, 14);
    const mh = macdHistAt(closes);
    const avgVol = candles.slice(-21, -1).reduce((s, c) => s + (c.v || 0), 0) / 20;
    const rvol = avgVol > 0 ? (candles[i].v || 0) / avgVol : 0;
    const { dir, line } = supertrend(candles, 10, 3);
    const flipUp = dir[i] === 1 && dir[i - 1] === -1, flipDn = dir[i] === -1 && dir[i - 1] === 1;
    const hiBreak = price > Math.max.apply(null, candles.slice(-11, -1).map(c => c.h));
    const loBreak = price < Math.min.apply(null, candles.slice(-11, -1).map(c => c.l));

    const longScore = (htf === "UP" ? 15 : 0) + (e20[i] > e50[i] && e50[i] > e200[i] ? 15 : 0) + (price > vw ? 15 : 0) + (adx >= 20 && plusDI > minusDI ? 15 : 0) +
      (rvol >= 1.1 ? 10 : 0) + (r >= 52 && r <= 70 ? 10 : 0) + (mh.hist > 0 && mh.hist > mh.prev ? 10 : 0) + ((flipUp || hiBreak) ? 10 : 0);
    const shortScore = (htf === "DOWN" ? 15 : 0) + (e20[i] < e50[i] && e50[i] < e200[i] ? 15 : 0) + (price < vw ? 15 : 0) + (adx >= 20 && minusDI > plusDI ? 15 : 0) +
      (rvol >= 1.1 ? 10 : 0) + (r <= 48 && r >= 30 ? 10 : 0) + (mh.hist < 0 && mh.hist < mh.prev ? 10 : 0) + ((flipDn || loBreak) ? 10 : 0);

    // Structural stop = min(Supertrend line, 5-bar swing low); validated 1-2.5 ATR.
    const swingLow = Math.min.apply(null, candles.slice(-6, -1).map(c => c.l));
    const swingHigh = Math.max.apply(null, candles.slice(-6, -1).map(c => c.h));
    const stL = line[i] != null ? line[i] : price - 1.5 * a;
    const longStop = Math.min(stL, swingLow);
    const shortStop = Math.max(stL, swingHigh);
    const longDist = price - longStop, shortDist = shortStop - price;
    const longStopOK = longDist >= a * 1.0 && longDist <= a * 2.5;
    const shortStopOK = shortDist >= a * 1.0 && shortDist <= a * 2.5;

    const trend = longScore > shortScore && longScore >= 50 ? "UP" : shortScore > longScore && shortScore >= 50 ? "DOWN" : "FLAT";
    let action = "HOLD", levels = null;
    if (longScore >= minScore && adx >= 20 && plusDI > minusDI && (htf !== "DOWN") && longStopOK) {
      const risk = price - longStop;
      action = "BUY"; levels = { entry: price, stop: longStop, target1: price + 1.5 * risk, target2: price + 3 * risk };
    } else if (shortScore >= minScore && adx >= 20 && minusDI > plusDI && (htf !== "UP") && shortStopOK) {
      const risk = shortStop - price;
      action = "SELL"; levels = { entry: price, stop: shortStop, target1: price - 1.5 * risk, target2: price - 3 * risk };
    }
    return { action, score: Math.max(longScore, shortScore), longScore, shortScore,
             price: +price.toFixed(2), rsi: +r.toFixed(0), adx: +adx.toFixed(0), rvol: +rvol.toFixed(1), trend, htf, levels };
  }

  // VIM — Volatility-normalized Intraday Momentum (parity with vim_nse.pine). The
  // math-first engine: trade only in the opening-range + VWAP momentum regime, enter on a
  // reversal pullback to EMA9 then resumption (better fills), fixed 1.2-ATR stop = 1R,
  // vol-targeted share size. Returns the same shape as swingDecide so the UI is unchanged;
  // target1 = +1R (the "scale 1/3 + trail" point), target2 = +3R (runner proxy).
  // Prior-session key levels (pivot + Camarilla R3/R4/S3/S4 + prior H/L) from the
  // candle history — the same levels the pre-market briefing reports. Returns null if
  // the series doesn't contain a prior session yet.
  function priorDayLevels(candles) {
    const key = (c) => new Date(c.t * 1000).toISOString().slice(0, 10);
    const days = [];
    for (const c of candles) { const k = key(c); if (days[days.length - 1] !== k) days.push(k); }
    if (days.length < 2) return null;
    const prevKey = days[days.length - 2];
    const pc = candles.filter(c => key(c) === prevKey);
    if (!pc.length) return null;
    const h = Math.max.apply(null, pc.map(c => c.h));
    const l = Math.min.apply(null, pc.map(c => c.l));
    const c = pc[pc.length - 1].c, rng = h - l, pivot = (h + l + c) / 3;
    return { pivot, r3: c + rng * 1.1 / 4, r4: c + rng * 1.1 / 2, s3: c - rng * 1.1 / 4, s4: c - rng * 1.1 / 2, dayHigh: h, dayLow: l };
  }

  // opts = { interval (min/bar), orMinutes, equity, riskPct }.
  function vimDecide(candles, opts) {
    opts = opts || {};
    const interval = opts.interval || 5;
    const orMin = opts.orMinutes || 20;
    const stopATRm = opts.stopATR || 1.2;
    const minADX = opts.minADX != null ? opts.minADX : 20;
    const r1 = opts.r1 || 1.0, r2 = opts.r2 || 3.0;
    const emaLen = 9;
    if (!candles || candles.length < 30) return { action: "WAIT", note: "loading candles…", score: 0, trend: "?" };
    const closes = candles.map(c => c.c);
    const i = closes.length - 1, price = closes[i];
    const a = atr(candles) || price * 0.003;
    const e = ema(closes, emaLen);
    const vw = sessionVwap(candles);
    const r = rsi(closes) || 50;
    const { adx, plusDI, minusDI } = adxDMI(candles, 14);

    // Opening range on the latest day; regime = OR direction confirmed by side of VWAP.
    const lastDay = new Date(candles[i].t * 1000).getUTCDate();
    const today = candles.filter(c => new Date(c.t * 1000).getUTCDate() === lastDay);
    const orBars = Math.max(1, Math.ceil(orMin / interval));
    let regime = "none", orHigh = null, orLow = null;
    if (today.length > orBars) {
      const orC = today.slice(0, orBars);
      orHigh = Math.max.apply(null, orC.map(c => c.h));
      orLow = Math.min.apply(null, orC.map(c => c.l));
      const orDir = orC[orC.length - 1].c > orC[0].o ? 1 : orC[orC.length - 1].c < orC[0].o ? -1 : 0;
      if (orDir > 0 && price > vw) regime = "up";
      else if (orDir < 0 && price < vw) regime = "down";
    }

    // Reversal-timed trigger: a pullback to EMA occurred recently AND price now resumes.
    const lookback = 6;
    let pulledUp = false, pulledDn = false;
    for (let k = Math.max(1, i - lookback); k <= i; k++) {
      if (candles[k].l <= e[k]) pulledUp = true;
      if (candles[k].h >= e[k]) pulledDn = true;
    }
    const crossUp = closes[i - 1] <= e[i - 1] && price > e[i];
    const crossDn = closes[i - 1] >= e[i - 1] && price < e[i];

    const risk = stopATRm * a;
    let action = "HOLD", levels = null;
    if (regime === "up" && pulledUp && crossUp && adx >= minADX && plusDI > minusDI) {
      action = "BUY";
      levels = { entry: price, stop: price - risk, target1: price + r1 * risk, target2: price + r2 * risk };
    } else if (regime === "down" && pulledDn && crossDn && adx >= minADX && minusDI > plusDI) {
      action = "SELL";
      levels = { entry: price, stop: price + risk, target1: price - r1 * risk, target2: price - r2 * risk };
    }

    // LEVEL FUSION: block a signal that fires into a wall — a long with no room up to
    // the nearest resistance, or a short with no floor down to the nearest support.
    // "Room" must cover at least most of the way to Target 1, else the trade can't work.
    const lv = priorDayLevels(candles);
    let wallNote = "";
    let nearRes = null, nearSup = null;
    if (lv) {
      const resAbove = [lv.r3, lv.r4, lv.dayHigh, lv.pivot].filter(x => x > price).sort((x, y) => x - y);
      const supBelow = [lv.s3, lv.s4, lv.dayLow, lv.pivot].filter(x => x < price).sort((x, y) => y - x);
      nearRes = resAbove.length ? resAbove[0] : null;
      nearSup = supBelow.length ? supBelow[0] : null;
      const need = 0.8 * r1 * risk;   // must have ~80% of the T1 distance clear
      if (action === "BUY" && nearRes != null && (nearRes - price) < need) {
        action = "HOLD"; levels = null; wallNote = "blocked: resistance ₹" + nearRes.toFixed(1) + " too close (no room to T1)";
      } else if (action === "SELL" && nearSup != null && (price - nearSup) < need) {
        action = "HOLD"; levels = null; wallNote = "blocked: support ₹" + nearSup.toFixed(1) + " too close (no room to T1)";
      }
    }

    const equity = opts.equity || 100000, riskPct = opts.riskPct || 0.5;
    const shares = risk > 0 ? Math.floor((riskPct / 100 * equity) / risk) : 0;
    const trend = regime === "up" ? "UP" : regime === "down" ? "DOWN" : "FLAT";
    let score = 0;
    if (regime !== "none") {
      score = 50;
      if ((regime === "up" && adx >= 20 && plusDI > minusDI) || (regime === "down" && adx >= 20 && minusDI > plusDI)) score += 25;
      if (action !== "HOLD") score += 25;
    }
    return {
      action, score, longScore: regime === "up" ? score : 0, shortScore: regime === "down" ? score : 0,
      price: +price.toFixed(2), rsi: +r.toFixed(0), adx: +adx.toFixed(0), rvol: 0, trend,
      regime, orHigh, orLow, shares, risk: +risk.toFixed(2), levels,
      keyLevels: lv, nearRes: nearRes != null ? +nearRes.toFixed(1) : null, nearSup: nearSup != null ? +nearSup.toFixed(1) : null,
      note: wallNote ? wallNote
        : regime === "none" ? "no regime yet (choppy / range still forming)"
        : action === "HOLD" ? ("" + regime + " regime — waiting for pullback→resume")
          : "entry trigger near levels — " + regime + " regime"
    };
  }

  const api = { ema, rsi, atr, sessionVwap, adxDMI, supertrend, htfTrendOf, decide, swingDecide, vimDecide, ALGOS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Algos = api;
})(typeof window !== "undefined" ? window : this);
