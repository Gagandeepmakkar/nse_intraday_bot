// Backtest the LIVE extension strategy (algos.js signalDecide) over historical 5m candles,
// with the SAME trade management as content.js (enter on closed-bar signal, 1/3 booked at
// T1, stop to breakeven), plus a switch to compare the current CAP-at-T2 exit against a
// CONVEX trailing exit. Sweeps minScore / stopATR / r1 / r2 / exit mode. Costs included.
// Usage: node backtest_live.js [datadir=/tmp/bt]
const fs = require("fs");
const A = require("./extension/algos.js");

const DIR = process.argv[2] || "/tmp/bt";
const COST_PCT = 0.0010;   // ~0.1% round-trip (brokerage+STT+slippage on liquid names)
const WIN = 250;           // bars of history fed to the strategy each step

const files = fs.readdirSync(DIR).filter(f => f.endsWith(".json"));
const data = {};
for (const f of files) data[f.replace(".json", "")] = JSON.parse(fs.readFileSync(DIR + "/" + f, "utf8"));

const dayKey = (t) => new Date(t * 1000).toISOString().slice(0, 10);
function atr(c, i, p = 14) {
  let s = 0, n = 0;
  for (let k = Math.max(1, i - p + 1); k <= i; k++) { const h = c[k].h, l = c[k].l, pc = c[k - 1].c; s += Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc)); n++; }
  return n ? s / n : 0;
}

// Run one parameter set over one symbol's candles. Returns array of per-trade R (net of cost).
function runSymbol(c, P) {
  const n = c.length, trades = [];
  let pos = null;
  for (let i = WIN; i < n; i++) {
    const bar = c[i];
    const eod = i === n - 1 || dayKey(c[i + 1].t) !== dayKey(bar.t);
    // manage open position with this bar
    if (pos) {
      const up = pos.side === 1;
      let exitR = null, done = false;
      const hitStop = up ? bar.l <= pos.stop : bar.h >= pos.stop;
      if (hitStop) {
        const r = up ? (pos.stop - pos.entry) / pos.risk : (pos.entry - pos.stop) / pos.risk;
        exitR = pos.booked + pos.liveQty * r; done = true;
      } else {
        // T1: book 1/3, stop -> breakeven
        if (!pos.t1done) {
          const hitT1 = up ? bar.h >= pos.t1 : bar.l <= pos.t1;
          if (hitT1) { pos.t1done = true; pos.booked = (1 / 3) * P.r1; pos.liveQty = 2 / 3; pos.stop = pos.entry; }
        }
        // after (or with) T1, exit logic depends on mode
        if (pos.t1done) {
          if (P.exit === "cap") {
            const hitT2 = up ? bar.h >= pos.t2 : bar.l <= pos.t2;
            if (hitT2) { exitR = pos.booked + pos.liveQty * P.r2; done = true; }
          } else { // trail
            const a = atr(c, i);
            if (up) { pos.hh = Math.max(pos.hh, bar.h); pos.stop = Math.max(pos.entry, pos.hh - P.trailATR * a); }
            else { pos.ll = Math.min(pos.ll, bar.l); pos.stop = Math.min(pos.entry, pos.ll + P.trailATR * a); }
          }
        }
      }
      if (!done && eod) { // square off at close
        const r = up ? (bar.c - pos.entry) / pos.risk : (pos.entry - bar.c) / pos.risk;
        exitR = pos.t1done ? pos.booked + pos.liveQty * r : r; done = true;
      }
      if (done) { trades.push(exitR - COST_PCT * pos.entry / pos.risk); pos = null; }
    }
    // consider a new entry at the close of bar i (signal on bars <= i)
    if (!pos && !eod) {
      const d = A.signalDecide(c.slice(Math.max(0, i - WIN), i + 1),
        { minScore: P.minScore, stopATR: P.stopATR, r1: P.r1, r2: P.r2, news: {} });
      if (d.levels && (d.action === "BUY" || d.action === "SELL")) {
        const side = d.action === "BUY" ? 1 : -1;
        pos = { side, entry: d.levels.entry, stop: d.levels.stop, t1: d.levels.target1, t2: d.levels.target2,
          risk: Math.abs(d.levels.entry - d.levels.stop) || 1, t1done: false, booked: 0, liveQty: 1,
          hh: bar.h, ll: bar.l };
      }
    }
  }
  return trades;
}

function stats(R) {
  const n = R.length;
  if (!n) return { n: 0 };
  const wins = R.filter(r => r > 0), losses = R.filter(r => r <= 0);
  const tot = R.reduce((a, b) => a + b, 0), gw = wins.reduce((a, b) => a + b, 0), gl = -losses.reduce((a, b) => a + b, 0);
  let run = 0, peak = 0, mdd = 0;
  for (const r of R) { run += r; peak = Math.max(peak, run); mdd = Math.min(mdd, run - peak); }
  return { n, exp: tot / n, pf: gl > 0 ? gw / gl : Infinity, win: wins.length / n, tot, mdd };
}

function runAll(P) {
  let R = [];
  for (const s in data) R = R.concat(runSymbol(data[s], P));
  return stats(R);
}

// ---- Sweep ----
console.log("symbols:", Object.keys(data).length, "| cost/round-trip:", (COST_PCT * 100).toFixed(2) + "%\n");
const grid = [];
for (const exit of ["cap", "trail"])
  for (const minScore of [55, 60, 70])
    for (const stopATR of [1.0, 1.5, 2.0])
      for (const r1 of [1.0, 1.5])
        grid.push({ exit, minScore, stopATR, r1, r2: 3.0, trailATR: 3.0 });

const rows = grid.map(P => ({ P, s: runAll(P) })).filter(x => x.s.n > 30);
rows.sort((a, b) => b.s.exp - a.s.exp);
console.log("TOP 12 parameter sets by expectancy (R/trade), net of cost:\n");
console.log("exit   minScore stopATR r1   | trades  exp(R)  PF    win%   totR   maxDD");
for (const { P, s } of rows.slice(0, 12))
  console.log(`${P.exit.padEnd(6)} ${String(P.minScore).padEnd(8)} ${String(P.stopATR).padEnd(7)} ${String(P.r1).padEnd(4)} | ${String(s.n).padEnd(6)} ${s.exp.toFixed(3).padStart(6)} ${(s.pf === Infinity ? "inf" : s.pf.toFixed(2)).padStart(5)} ${(s.win * 100).toFixed(0).padStart(4)}% ${s.tot.toFixed(0).padStart(6)} ${s.mdd.toFixed(1).padStart(7)}`);
// head-to-head: same params, cap vs trail
console.log("\nCAP vs TRAIL (minScore 60, stopATR 1.5, r1 1.5):");
for (const exit of ["cap", "trail"]) {
  const s = runAll({ exit, minScore: 60, stopATR: 1.5, r1: 1.5, r2: 3.0, trailATR: 3.0 });
  console.log(`  ${exit.padEnd(6)} trades ${s.n}  exp ${s.exp.toFixed(3)}R  PF ${s.pf === Infinity ? "inf" : s.pf.toFixed(2)}  win ${(s.win * 100).toFixed(0)}%  totR ${s.tot.toFixed(0)}  maxDD ${s.mdd.toFixed(1)}`);
}
