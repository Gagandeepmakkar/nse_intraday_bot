"use strict";
const E = window.BTCEngine;
const SYMS = ["BTC", "ETH"];
const DEFAULTS = { interval: "1d", period: 20, allowShort: true, ntfyTopic: "" };
let CFG = Object.assign({}, DEFAULTS);
const $ = (id) => document.getElementById(id);
const fmt = (n) => n >= 1000 ? n.toLocaleString("en-US", { maximumFractionDigits: 0 }) : n.toFixed(2);
const cell = (k, v, c) => `<div class="lc ${c || ""}"><span>${k}</span><b>${v}</b></div>`;

function render(states) {
  $("mode").textContent = "DAILY" + (CFG.allowShort ? " · L/S" : " · long");
  $("rows").innerHTML = states.map((st, i) => {
    const label = SYMS[i];
    if (!st || st.err) return `<div class="det">${label}: ${(st && st.err) || "no data"}</div>`;
    const pos = st.pos === 1 ? "LONG" : st.pos === -1 ? "SHORT" : "FLAT";
    const cls = st.pos === 1 ? "long" : st.pos === -1 ? "short" : "flat";
    const pnl = st.pos ? `<b class="${st.pnl >= 0 ? "pos" : "neg"}">${st.pnl >= 0 ? "+" : ""}${st.pnl.toFixed(1)}%</b>` : "";
    let grid, note;
    if (!st.pos) {
      grid = cell("LONG if &gt;", "$" + fmt(st.upBreak), "up") + (CFG.allowShort ? cell("SHORT if &lt;", "$" + fmt(st.dnBreak), "dn") : "");
      note = "flat — waiting for a DAILY close beyond the level.";
    } else {
      const fresh = st.entryBarsAgo <= 5;
      grid = cell("Entry", "$" + fmt(st.entry)) + cell(fresh ? "Stop" : "Trail", "$" + fmt(st.stop), "dn")
           + cell("T1", "$" + fmt(st.t1), "up") + cell("T2", "$" + fmt(st.t2), "up");
      note = fresh
        ? `fresh ${pos} — stop $${fmt(st.stop)} (targets are a reference; real exit is the trail)`
        : `${pos} · ${st.entryBarsAgo} bars in · trailing stop $${fmt(st.stop)}`;
    }
    return `<div class="row ${cls}"><div class="r1"><span class="sym">${label}</span><span class="badge ${cls}">${pos}</span><span class="px">$${fmt(st.px)}</span>${pnl}</div><div class="lv">${grid}</div><div class="det">${note}</div></div>`;
  }).join("");
}

async function refresh() {
  $("mode").textContent = "loading…";
  // Fetch each symbol independently so one failure doesn't blank the other. (Fixes the
  // old bug where `([s])` destructured "BTC" to "B" and fetched an invalid symbol.)
  const states = await Promise.all(SYMS.map(async (s) => {
    try { return await E.state(s, CFG); } catch (e) { return { err: String(e && e.message || e) }; }
  }));
  render(states);
}

chrome.storage.local.get(["btccfg"], (r) => {
  CFG = Object.assign({}, DEFAULTS, r.btccfg || {});
  CFG.interval = "1d";   // daily only
  $("short").checked = CFG.allowShort;
  $("ntfy").value = CFG.ntfyTopic;
  refresh();
});

$("save").onclick = () => {
  CFG.interval = "1d";
  CFG.allowShort = $("short").checked;
  CFG.ntfyTopic = $("ntfy").value.trim();
  chrome.storage.local.set({ btccfg: CFG }, refresh);
};
