"use strict";
const E = window.BTCEngine;
const SYMS = ["BTC", "ETH"];
const DEFAULTS = { interval: "1d", period: 20, allowShort: true, ntfyTopic: "" };
let CFG = Object.assign({}, DEFAULTS);
const $ = (id) => document.getElementById(id);
const fmt = (n) => n >= 1000 ? n.toLocaleString("en-US", { maximumFractionDigits: 0 }) : n.toFixed(2);

function render(states) {
  $("mode").textContent = CFG.interval + (CFG.allowShort ? " L/S" : " L");
  $("rows").innerHTML = states.map((st, i) => {
    const label = SYMS[i];
    const pos = st.pos === 1 ? "LONG" : st.pos === -1 ? "SHORT" : "FLAT";
    const cls = st.pos === 1 ? "long" : st.pos === -1 ? "short" : "flat";
    const pnl = st.pos ? `<b class="${st.pnl >= 0 ? "pos" : "neg"}">${st.pnl >= 0 ? "+" : ""}${st.pnl.toFixed(1)}%</b>` : "";
    const detail = st.pos
      ? `entry $${fmt(st.entry)} · stop $${fmt(st.stop)}${st.t1 ? ` · T1 $${fmt(st.t1)} · T2 $${fmt(st.t2)}` : ""}`
      : `waiting — LONG if close &gt; $${fmt(st.upBreak)}${CFG.allowShort ? ` · SHORT if &lt; $${fmt(st.dnBreak)}` : ""}`;
    return `<div class="row"><div class="r1"><span class="sym">${label}</span><span class="badge ${cls}">${pos}</span><span class="px">$${fmt(st.px)}</span>${pnl}</div><div class="det">${detail}</div></div>`;
  }).join("");
}

async function refresh() {
  try { render(await Promise.all(SYMS.map(([s]) => E.state(s, CFG)))); }
  catch (e) { $("rows").innerHTML = '<div class="det">Binance unreachable — retry.</div>'; }
}

chrome.storage.local.get(["btccfg"], (r) => {
  CFG = Object.assign({}, DEFAULTS, r.btccfg || {});
  $("int").value = CFG.interval; $("short").checked = CFG.allowShort; $("ntfy").value = CFG.ntfyTopic;
  refresh();
});
$("save").onclick = () => {
  CFG.interval = $("int").value; CFG.allowShort = $("short").checked; CFG.ntfyTopic = $("ntfy").value.trim();
  chrome.storage.local.set({ btccfg: CFG }, () => { $("mode").textContent = "saved ✓"; refresh(); });
};
