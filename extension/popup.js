"use strict";
// Toolbar popup: mirrors the on-chart settings + shows today's stats and journal.
// Everything is backed by chrome.storage.local, shared live with the content script.

const DEFAULTS = {
  strategy: "vim", interval: 5, orMinutes: 20, minADX: 20, stopATR: 1.2, r1: 1.0, r2: 3.0,
  equity: 100000, riskPct: 0.5, sound: true, alerts: true, maxTrades: 3, maxLossR: 2.0,
  tgToken: "", tgChat: ""
};
const $ = (id) => document.getElementById(id);
const NUMS = ["equity", "riskPct", "orMinutes", "minADX", "stopATR", "r1", "r2", "maxTrades", "maxLossR"];
const SELS = ["strategy", "interval"];
const TXTS = ["tgToken", "tgChat"];
const CHKS = ["sound", "alerts"];
let CFG = Object.assign({}, DEFAULTS);

function todayStr() { const d = new Date(); return d.getFullYear() + "-" + (d.getMonth() + 1) + "-" + d.getDate(); }

function load() {
  chrome.storage.local.get(["cfg", "stats", "journal"], (r) => {
    CFG = Object.assign({}, DEFAULTS, r.cfg || {});
    SELS.forEach((k) => $(k).value = String(CFG[k]));
    NUMS.forEach((k) => $(k).value = CFG[k]);
    TXTS.forEach((k) => $(k).value = CFG[k]);
    CHKS.forEach((k) => $(k).checked = !!CFG[k]);
    renderStats((r.stats && r.stats.day === todayStr()) ? r.stats : null);
    renderJournal(r.journal || []);
  });
}

function renderStats(s) {
  if (!s) { $("stats").innerHTML = '<div class="st"><span>No trades today</span></div>'; return; }
  const n = s.trades, w = s.wins, l = s.losses, p = n ? w / n : 0;
  const aw = w ? s.sumWinR / w : 0, al = l ? s.sumLossR / l : 0;
  const exp = n ? p * aw - (1 - p) * al : 0, payoff = al > 0 ? aw / al : 0;
  const kelly = payoff > 0 ? p - (1 - p) / payoff : 0;
  const cls = (v) => v > 0 ? "pos" : v < 0 ? "neg" : "";
  $("stats").innerHTML =
    `<div class="st"><span>Day R</span><b class="${cls(s.cumR)}">${s.cumR.toFixed(2)}</b></div>` +
    `<div class="st"><span>Trades</span><b>${n} (${w}/${l})</b></div>` +
    `<div class="st"><span>Win%</span><b>${n ? Math.round(p * 100) : "–"}</b></div>` +
    `<div class="st"><span>Exp</span><b class="${cls(exp)}">${n ? exp.toFixed(2) + "R" : "–"}</b></div>` +
    `<div class="st"><span>¼Kelly</span><b class="${cls(kelly)}">${n ? Math.round(Math.max(0, kelly) * 25) + "%" : "–"}</b></div>`;
}

function renderJournal(j) {
  if (!j.length) { $("journal").innerHTML = '<div class="je muted">No trades yet.</div>'; return; }
  $("journal").innerHTML = j.slice(-10).reverse().map((x) => {
    const t = new Date(x.t); const hh = String(t.getHours()).padStart(2, "0") + ":" + String(t.getMinutes()).padStart(2, "0");
    return `<div class="je"><span>${hh} ${x.side} ${x.sym}</span><b class="${x.R > 0 ? "pos" : x.R < 0 ? "neg" : ""}">${x.R > 0 ? "+" : ""}${x.R}R <i>${x.exit}</i></b></div>`;
  }).join("");
}

function save() {
  SELS.forEach((k) => CFG[k] = k === "interval" ? parseInt($(k).value, 10) : $(k).value);
  NUMS.forEach((k) => { const v = parseFloat($(k).value); if (!isNaN(v)) CFG[k] = v; });
  TXTS.forEach((k) => CFG[k] = $(k).value.trim());
  CHKS.forEach((k) => CFG[k] = $(k).checked);
  chrome.storage.local.set({ cfg: CFG }, () => { $("status").textContent = "saved ✓"; setTimeout(() => $("status").textContent = "today", 1200); });
}

function exportCsv() {
  chrome.storage.local.get(["journal"], (r) => {
    const j = r.journal || [];
    const rows = [["datetime", "symbol", "side", "entry", "stop", "exit", "R"]];
    j.forEach((x) => rows.push([new Date(x.t).toISOString(), x.sym, x.side, x.entry, x.stop, x.exit, x.R]));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([rows.map((r) => r.join(",")).join("\n")], { type: "text/csv" }));
    a.download = "vim_journal_" + todayStr() + ".csv"; a.click();
  });
}

$("save").onclick = save;
$("exp").onclick = exportCsv;
$("clr").onclick = () => {
  if (!confirm("Reset today's stats and journal?")) return;
  chrome.storage.local.set({ stats: { day: todayStr(), trades: 0, wins: 0, losses: 0, cumR: 0, sumWinR: 0, sumLossR: 0 }, journal: [] }, load);
};
$("tgtest").onclick = () => {
  const token = $("tgToken").value.trim();
  let chat = $("tgChat").value.trim();
  if (!token) { $("status").textContent = "token missing"; return; }
  const send = (c) => chrome.runtime.sendMessage({ __tg: true, token, chat: c, text: "✅ VIM test alert — Telegram connected." }, (r) => {
    if (r && r.ok) $("status").textContent = "delivered ✓ — check Telegram";
    else $("status").textContent = "FAILED: " + ((r && r.desc) || "start the bot & check chat id");
  });
  if (chat) { send(chat); return; }
  $("status").textContent = "connecting… (press Start in the bot first)";
  chrome.runtime.sendMessage({ __tgConnect: true, token }, (r) => {
    if (r && r.ok && r.chat) { $("tgChat").value = r.chat; CFG.tgChat = r.chat; chrome.storage.local.set({ cfg: CFG }); send(r.chat); }
    else $("status").textContent = "press Start in your bot, then retry";
  });
};
chrome.storage.onChanged.addListener((ch, area) => { if (area === "local" && (ch.stats || ch.journal)) load(); });
load();
