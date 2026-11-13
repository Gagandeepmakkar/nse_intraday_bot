// VIM Live — Groww trade assistant (content script), production build.
// Price + signals come from Groww's OWN candle feed (captured by inject_hook.js) — never
// from scraping the page. Signals are confirmed on CLOSED bars (no repaint); exits use the
// forming bar's high/low. Everything runs locally in your browser. Confirmation only.
(function () {
  "use strict";
  if (window.__vimLoaded) return;
  window.__vimLoaded = true;

  const IST = 19800;
  const BETTER_MARGIN = 10;

  const S = {
    url: "", auth: "", symbol: "", interval: 5,
    phase: 0, entry: 0, stop: 0, t1: 0, t2: 0, t1done: false, eScore: 0, entry0: 0, stop0: 0,
    chart: null, series: null, lines: [], ro: null, panel: null,
    price: 0, shares: 0, lastOk: 0, blocked: false, news: {}, lastBetterAt: 0
  };

  // ---- Persisted config + UI geometry ----
  const DEFAULTS = {
    strategy: "signals", minScore: 60, interval: 5, orMinutes: 20, minADX: 20,
    stopATR: 1.5, r1: 1.5, r2: 3.0, equity: 100000, riskPct: 0.5,
    sound: true, alerts: true, maxTrades: 3, maxLossR: 2.0,
    ntfyTopic: "", tgToken: "", tgChat: ""
  };
  let CFG = Object.assign({}, DEFAULTS), UI = null;
  function loadCfg(cb) {
    try { chrome.storage.local.get(["cfg", "ui"], (r) => { CFG = Object.assign({}, DEFAULTS, r.cfg || {}); UI = r.ui || null; S.interval = CFG.interval; if (cb) cb(); }); }
    catch (e) { if (cb) cb(); }
  }
  function saveCfg() { try { chrome.storage.local.set({ cfg: CFG }); } catch (e) {} }
  function saveGeom() {
    if (!S.panel) return;
    const r = S.panel.getBoundingClientRect();
    UI = { left: r.left, top: r.top, w: r.width, h: r.height };
    try { chrome.storage.local.set({ ui: UI }); } catch (e) {}
  }

  // ---- Stats + journal ----
  function todayStr() { const d = new Date(); return d.getFullYear() + "-" + (d.getMonth() + 1) + "-" + d.getDate(); }
  function freshStats() { return { day: todayStr(), trades: 0, wins: 0, losses: 0, cumR: 0, sumWinR: 0, sumLossR: 0 }; }
  let STATS = freshStats(), JOURNAL = [];
  function loadStats(cb) {
    try { chrome.storage.local.get(["stats", "journal"], (r) => { STATS = (r.stats && r.stats.day === todayStr()) ? r.stats : freshStats(); JOURNAL = r.journal || []; if (cb) cb(); }); }
    catch (e) { if (cb) cb(); }
  }
  function saveStats() { try { chrome.storage.local.set({ stats: STATS, journal: JOURNAL.slice(-500) }); } catch (e) {} }
  function rolloverIfNewDay() { if (STATS.day !== todayStr()) { STATS = freshStats(); saveStats(); renderStats(); } }

  try {
    chrome.storage.onChanged.addListener((ch, area) => {
      if (area !== "local") return;
      if (ch.cfg) { CFG = Object.assign({}, DEFAULTS, ch.cfg.newValue || {}); S.interval = CFG.interval; syncSettingsUI(); }
      if (ch.stats && ch.stats.newValue) { STATS = ch.stats.newValue; renderStats(); }
      if (ch.journal) { JOURNAL = ch.journal.newValue || []; }
    });
  } catch (e) {}

  window.addEventListener("message", (e) => {
    const d = e.data;
    if (!(d && d.__nlcGroww && d.url && d.url.indexOf("charting_service") !== -1)) return;
    S.url = d.url;
    if (d.auth) S.auth = d.auth;
    const m = d.url.match(/\/CASH\/([^/]+)\/daily/);
    if (m && m[1] !== S.symbol) {
      if (S.phase !== 0) closeTrade(floatingR(), "SWITCH");
      S.symbol = m[1]; S.price = 0; S.news = {}; reset(); fetchNews();
    }
  });
  function fetchNews() {
    if (!S.symbol) return;
    const sym = S.symbol;
    try { chrome.runtime.sendMessage({ __news: true, symbol: sym }, (r) => { if (r && sym === S.symbol) S.news = r; }); } catch (e) {}
  }

  function reset() { S.phase = 0; S.t1done = false; S.eScore = 0; S.entry = S.stop = S.t1 = S.t2 = S.entry0 = S.stop0 = 0; }
  function floatingR() {
    if (S.phase === 0 || !S.entry0) return 0;
    const px = S.price || S.entry0, risk = Math.abs(S.entry0 - S.stop0) || 1;
    const raw = S.phase === 1 ? (px - S.entry0) / risk : (S.entry0 - px) / risk;
    return +(S.t1done ? (CFG.r1 / 3 + (2 / 3) * raw) : raw).toFixed(2);
  }

  // ---- alerts ----
  function notify(title, body) {
    if (!CFG.alerts) return;
    try { if (window.Notification && Notification.permission === "granted") new Notification(title, { body, silent: !CFG.sound }); } catch (e) {}
    if (CFG.sound) beep();
    try { if (CFG.tgToken && CFG.tgChat) chrome.runtime.sendMessage({ __tg: true, token: CFG.tgToken, chat: CFG.tgChat, text: title + " — " + body }); } catch (e) {}
    try { if (CFG.ntfyTopic) chrome.runtime.sendMessage({ __ntfy: true, topic: CFG.ntfyTopic, title, text: body }); } catch (e) {}
  }
  function logEvent(ev) { try { chrome.runtime.sendMessage({ __log: true, events: [ev] }); } catch (e) {} }
  function syncJournal() { if (JOURNAL.length) try { chrome.runtime.sendMessage({ __log: true, events: JOURNAL.map(j => Object.assign({ event: "close" }, j)) }); } catch (e) {} }
  function beep() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.connect(g); g.connect(ctx.destination); o.type = "sine"; o.frequency.value = 880; g.gain.value = 0.08;
      o.start(); setTimeout(() => { o.stop(); ctx.close(); }, 170);
    } catch (e) {}
  }

  const $ = (id) => document.getElementById(id);
  const fnum = (id, label, val, step) => '<label class="vim-f"><span>' + label + '</span><input id="' + id + '" type="number" step="' + step + '" value="' + val + '"></label>';

  // ---- drag + resize (document handlers attached once) ----
  let DRAG = null, RZ = null;
  function onDocMove(e) {
    const p = S.panel; if (!p) return;
    if (DRAG) { p.style.left = (DRAG.ox + e.clientX - DRAG.sx) + "px"; p.style.top = (DRAG.oy + e.clientY - DRAG.sy) + "px"; p.style.right = "auto"; }
    else if (RZ) { p.style.width = Math.max(300, Math.min(innerWidth * 0.96, RZ.w + e.clientX - RZ.sx)) + "px"; p.style.height = Math.max(160, Math.min(innerHeight * 0.92, RZ.h + e.clientY - RZ.sy)) + "px"; resizeChart(); }
  }
  function onDocUp() { if (DRAG || RZ) saveGeom(); DRAG = null; RZ = null; }
  function resizeChart() { const dv = $("vim-chart"); if (S.chart && dv) S.chart.applyOptions({ width: dv.clientWidth }); }

  function build() {
    const p = document.createElement("div");
    p.id = "vim-panel";
    p.innerHTML =
      '<div id="vim-head">' +
        '<span id="vim-dot" class="off"></span><span id="vim-logo">VIM</span><span id="vim-sym">—</span>' +
        '<span id="vim-price">₹—</span><span id="vim-gear" class="ic" title="settings">⚙</span><span id="vim-min" class="ic" title="minimize">–</span>' +
      '</div>' +
      '<div id="vim-body">' +
        '<div id="vim-banner"></div>' +
        '<div id="vim-hero"><div id="vim-action" class="WAIT">—</div><div id="vim-score"><span id="vim-scoretext">0</span><div id="vim-scorebar"><div id="vim-scorefill"></div></div></div></div>' +
        '<div id="vim-reason"></div>' +
        '<div id="vim-factors"></div>' +
        '<div class="vim-grid" style="margin-bottom:11px">' +
          '<label class="vim-f"><span>Mode</span><select id="q-strat"><option value="signals">Signals</option><option value="vim">VIM</option><option value="scanner">Scanner</option></select></label>' +
          '<label class="vim-f"><span>Timeframe</span><select id="q-tf"><option value="3">3m</option><option value="5">5m</option><option value="15">15m</option></select></label>' +
        '</div>' +
        '<button id="vim-cta">🔔 Enable desktop alerts</button>' +
        '<div id="vim-levels"></div>' +
        '<div id="vim-chart"></div>' +
        '<div id="vim-meta"></div>' +
        '<div id="vim-stats"></div>' +
        '<div class="vim-sec" id="sec-journal"><div class="vim-sec-h" data-sec="journal"><span>Journal</span><span class="act"><button id="vim-exp" class="vim-mini">Export</button><button id="vim-clr" class="vim-mini">Reset</button><span class="vim-caret">▾</span></span></div><div id="vim-journal" class="vim-sec-b"></div></div>' +
        '<div class="vim-sec" id="sec-settings"><div class="vim-sec-h" data-sec="settings"><span>Settings</span><span class="vim-caret">▾</span></div>' +
          '<div id="vim-settings" class="vim-sec-b hidden">' +
            '<div class="vim-grid">' +
              fnum("set-equity", "Capital ₹", CFG.equity, "1000") + fnum("set-risk", "Risk %", CFG.riskPct, "0.1") +
              fnum("set-score", "Min signal score", CFG.minScore, "5") + fnum("set-adx", "Min ADX", CFG.minADX, "1") +
              fnum("set-stop", "Stop ATR", CFG.stopATR, "0.1") + fnum("set-or", "OR min (VIM)", CFG.orMinutes, "5") +
              fnum("set-r1", "Target1 R", CFG.r1, "0.5") + fnum("set-r2", "Target2 R", CFG.r2, "0.5") +
              fnum("set-maxt", "Max trades/day", CFG.maxTrades, "1") + fnum("set-maxl", "Daily stop (−R)", CFG.maxLossR, "0.5") +
            '</div>' +
            '<label class="vim-chk"><input type="checkbox" id="set-sound"> Sound on alerts</label>' +
            '<label class="vim-chk"><input type="checkbox" id="set-alerts"> Desktop / phone alerts</label>' +
            '<div class="vim-note">📱 Phone push — ntfy (no login, just a topic name):<input id="set-ntfy" placeholder="your secret topic e.g. gagan-vim-9f3k2"><button id="set-ntfytest" class="vim-mini" style="margin-top:6px">Test phone</button></div>' +
            '<div class="vim-note">Telegram (optional):<input id="set-tgtoken" placeholder="bot token"><input id="set-tgchat" placeholder="chat id" style="margin-top:6px"><button id="set-tgtest" class="vim-mini" style="margin-top:6px">Test</button></div>' +
            '<button id="set-save" class="vim-save">Save settings</button>' +
          '</div>' +
        '</div>' +
        '<div id="vim-foot">Price & signals from your Groww candle feed · signals confirmed on closed bars · paper-trade · NOT advice — execute manually.</div>' +
      '</div>' +
      '<div id="vim-resize" title="drag to resize">⤡</div>';
    document.body.appendChild(p);
    S.panel = p;
    if (UI) { if (UI.left != null) { p.style.left = UI.left + "px"; p.style.top = UI.top + "px"; p.style.right = "auto"; } if (UI.w) { p.style.width = UI.w + "px"; p.style.height = UI.h + "px"; } }
    if (S.ro) { try { S.ro.disconnect(); } catch (e) {} }
    try { S.ro = new ResizeObserver(resizeChart); S.ro.observe(p); } catch (e) {}

    $("vim-resize").addEventListener("mousedown", (e) => { const r = p.getBoundingClientRect(); RZ = { sx: e.clientX, sy: e.clientY, w: r.width, h: r.height }; e.preventDefault(); e.stopPropagation(); });
    $("vim-head").addEventListener("mousedown", (e) => { if (e.target.classList.contains("ic")) return; const r = p.getBoundingClientRect(); DRAG = { sx: e.clientX, sy: e.clientY, ox: r.left, oy: r.top }; e.preventDefault(); });
    $("vim-min").onclick = () => p.classList.toggle("min");
    $("vim-gear").onclick = () => toggleSec("settings");
    p.querySelectorAll(".vim-sec-h").forEach((h) => h.onclick = (ev) => { if (ev.target.tagName === "BUTTON") return; toggleSec(h.getAttribute("data-sec")); });

    const qs = $("q-strat"); qs.value = CFG.strategy; qs.onchange = () => { CFG.strategy = qs.value; saveCfg(); reset(); refresh(); };
    const qt = $("q-tf"); qt.value = String(S.interval); qt.onchange = () => { CFG.interval = S.interval = parseInt(qt.value, 10) || 5; saveCfg(); reset(); refresh(); };
    const cta = $("vim-cta");
    if (window.Notification && Notification.permission === "granted") cta.style.display = "none";
    cta.onclick = () => { try { Notification.requestPermission().then((r) => { if (r === "granted") { cta.style.display = "none"; notify("Alerts on", "You'll be pinged on BUY / SELL / targets."); } }); } catch (e) {} };
    $("vim-exp").onclick = exportCsv;
    $("vim-clr").onclick = () => { if (confirm("Reset today's stats and journal?")) { STATS = freshStats(); JOURNAL = []; saveStats(); renderStats(); renderJournal(); } };
    $("set-save").onclick = readSettingsUI;
    $("set-ntfytest").onclick = () => {
      CFG.ntfyTopic = $("set-ntfy").value.trim(); if (!CFG.ntfyTopic) return; saveCfg();
      try { chrome.runtime.sendMessage({ __ntfy: true, topic: CFG.ntfyTopic, title: "VIM test", text: "✅ Phone alerts work — subscribe to this topic in the ntfy app." }, (r) => setStatus(r && r.ok ? "ntfy ✓ sent — check phone" : "ntfy failed")); } catch (e) {}
    };
    $("set-tgtest").onclick = () => {
      CFG.tgToken = $("set-tgtoken").value.trim(); CFG.tgChat = $("set-tgchat").value.trim();
      if (!CFG.tgToken) { setStatus("paste the bot token"); return; }
      const done = (r) => setStatus(r && r.ok ? "Telegram ✓ delivered" : "Telegram FAILED: " + ((r && r.desc) || "start the bot / chat id"));
      if (CFG.tgChat) { saveCfg(); try { chrome.runtime.sendMessage({ __tg: true, token: CFG.tgToken, chat: CFG.tgChat, text: "✅ VIM test alert — Telegram connected." }, done); } catch (e) {} return; }
      setStatus("connecting Telegram…");
      try { chrome.runtime.sendMessage({ __tgConnect: true, token: CFG.tgToken }, (r) => { if (r && r.ok && r.chat) { CFG.tgChat = r.chat; $("set-tgchat").value = r.chat; saveCfg(); setStatus("Telegram ✓ connected"); } else setStatus("press Start in your bot, then retry"); }); } catch (e) {}
    };
    syncSettingsUI(); S.chart = null; renderStats(); renderJournal();
  }
  function toggleSec(name) {
    const sec = $("sec-" + name), body = $("vim-" + name);
    if (!sec || !body) return;
    body.classList.toggle("hidden"); sec.classList.toggle("open", !body.classList.contains("hidden"));
  }
  function setStatus(t) { const el = $("vim-sym"); if (el) { el.textContent = t; setTimeout(() => { if (el) el.textContent = S.symbol || "—"; }, 2500); } }

  function syncSettingsUI() {
    if (!$("set-equity")) return;
    $("set-equity").value = CFG.equity; $("set-risk").value = CFG.riskPct; $("set-score").value = CFG.minScore;
    $("set-adx").value = CFG.minADX; $("set-stop").value = CFG.stopATR; $("set-or").value = CFG.orMinutes;
    $("set-r1").value = CFG.r1; $("set-r2").value = CFG.r2; $("set-maxt").value = CFG.maxTrades; $("set-maxl").value = CFG.maxLossR;
    $("set-sound").checked = CFG.sound; $("set-alerts").checked = CFG.alerts;
    $("set-ntfy").value = CFG.ntfyTopic; $("set-tgtoken").value = CFG.tgToken; $("set-tgchat").value = CFG.tgChat;
    if ($("q-strat")) $("q-strat").value = CFG.strategy;
    if ($("q-tf")) $("q-tf").value = String(CFG.interval);
  }
  function readSettingsUI() {
    const n = (id, d) => { const v = parseFloat($(id).value); return isNaN(v) ? d : v; };
    CFG.equity = n("set-equity", CFG.equity); CFG.riskPct = n("set-risk", CFG.riskPct); CFG.minScore = n("set-score", CFG.minScore);
    CFG.minADX = n("set-adx", CFG.minADX); CFG.stopATR = n("set-stop", CFG.stopATR); CFG.orMinutes = n("set-or", CFG.orMinutes);
    CFG.r1 = n("set-r1", CFG.r1); CFG.r2 = n("set-r2", CFG.r2); CFG.maxTrades = n("set-maxt", CFG.maxTrades); CFG.maxLossR = n("set-maxl", CFG.maxLossR);
    CFG.sound = $("set-sound").checked; CFG.alerts = $("set-alerts").checked;
    CFG.ntfyTopic = $("set-ntfy").value.trim(); CFG.tgToken = $("set-tgtoken").value.trim(); CFG.tgChat = $("set-tgchat").value.trim();
    saveCfg(); toggleSec("settings"); refresh();
  }
  function ensurePanel() { if (!document.getElementById("vim-panel")) build(); }

  function ensureChart() {
    if (S.chart || !window.LightweightCharts) return;
    const div = $("vim-chart"); if (!div) return;
    S.chart = LightweightCharts.createChart(div, {
      height: 150, width: div.clientWidth,
      layout: { background: { color: "transparent" }, textColor: "#8b95a3" },
      grid: { vertLines: { color: "#141b24" }, horzLines: { color: "#141b24" } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#1b232e" },
      rightPriceScale: { borderColor: "#1b232e" }
    });
    S.series = S.chart.addCandlestickSeries({ upColor: "#3fcf6b", downColor: "#ff5b6b", borderVisible: false, wickUpColor: "#3fcf6b", wickDownColor: "#ff5b6b" });
  }
  function drawLevels() {
    if (!S.series) return;
    S.lines.forEach((l) => S.series.removePriceLine(l)); S.lines = [];
    if (S.phase === 0) return;
    const add = (price, color, title) => { if (price) S.lines.push(S.series.createPriceLine({ price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title })); };
    add(S.entry, "#4cc2ff", "Entry"); add(S.stop, "#ff5b6b", "SL"); add(S.t1, "#e0b341", "T1"); add(S.t2, "#3fcf6b", "T2");
  }

  async function fetchCandles(interval) {
    let url = S.url.replace("minimal=true", "minimal=false");
    if (url.indexOf("minimal=") === -1) url += (url.indexOf("?") === -1 ? "?" : "&") + "minimal=false";
    if (interval) url = url.replace(/intervalInMinutes=\d+/, "intervalInMinutes=" + interval);
    const headers = { "Accept": "application/json" };
    if (S.auth) { headers["Authorization"] = S.auth; headers["X-APP-ID"] = "growwWeb"; headers["x-platform"] = "web"; }
    const r = await fetch(url, { headers, credentials: "include" });
    if (!r.ok) throw new Error("Groww " + r.status);
    const j = await r.json();
    return (j.candles || []).map((a) => ({ t: a[0], o: a[1], h: a[2], l: a[3], c: a[4], v: a[5] }));
  }

  // Exits against the forming bar's high/low (catches any wick that touches a level).
  function checkExits(hi, lo) {
    if (S.phase === 1) {
      if (lo <= S.stop) { const R = S.t1done ? CFG.r1 / 3 : -1; notify((R > 0 ? "🟡 BE " : "⛔ STOP ") + S.symbol, R > 0 ? "Stopped at breakeven (+1/3 booked)" : "Exit — stop hit"); closeTrade(R, S.t1done ? "BE" : "SL"); }
      else { if (!S.t1done && hi >= S.t1) { S.t1done = true; S.stop = S.entry; notify("🎯 +1R " + S.symbol, "Booked 1/3; stop → breakeven, rest runs to T2"); } if (hi >= S.t2) { notify("✅ +" + CFG.r2 + "R " + S.symbol, "T2 hit — exit the rest"); closeTrade(CFG.r1 / 3 + (2 / 3) * CFG.r2, "T2"); } }
    } else if (S.phase === -1) {
      if (hi >= S.stop) { const R = S.t1done ? CFG.r1 / 3 : -1; notify((R > 0 ? "🟡 BE " : "⛔ STOP ") + S.symbol, R > 0 ? "Stopped at breakeven (+1/3 booked)" : "Exit — stop hit"); closeTrade(R, S.t1done ? "BE" : "SL"); }
      else { if (!S.t1done && lo <= S.t1) { S.t1done = true; S.stop = S.entry; notify("🎯 +1R " + S.symbol, "Booked 1/3; stop → breakeven, rest runs to T2"); } if (lo <= S.t2) { notify("✅ +" + CFG.r2 + "R " + S.symbol, "T2 hit — exit the rest"); closeTrade(CFG.r1 / 3 + (2 / 3) * CFG.r2, "T2"); } }
    }
  }
  function closeTrade(R, reason) {
    STATS.trades++; STATS.cumR += R;
    if (R > 0) { STATS.wins++; STATS.sumWinR += R; } else { STATS.losses++; STATS.sumLossR += -R; }
    const rec = { t: Date.now(), sym: S.symbol, side: S.phase === 1 ? "LONG" : "SHORT", entry: +S.entry0.toFixed(2), stop: +S.stop0.toFixed(2), exit: reason, R: +R.toFixed(2) };
    JOURNAL.push(rec); logEvent(Object.assign({ event: "close", tf: S.interval, strat: CFG.strategy }, rec));
    saveStats(); renderStats(); renderJournal(); reset();
  }

  function machine(forming, d) {
    S.blocked = STATS.trades >= CFG.maxTrades || STATS.cumR <= -Math.abs(CFG.maxLossR);
    if (S.phase === 0) {
      if (!S.blocked && d.levels && (d.action === "BUY" || d.action === "SELL")) {
        S.phase = d.action === "BUY" ? 1 : -1;
        S.entry = d.levels.entry; S.stop = d.levels.stop; S.t1 = d.levels.target1; S.t2 = d.levels.target2;
        S.entry0 = S.entry; S.stop0 = S.stop; S.t1done = false; S.eScore = d.score;
        logEvent({ event: "signal", t: Date.now(), sym: S.symbol, side: d.action === "BUY" ? "LONG" : "SHORT", entry: +S.entry.toFixed(2), stop: +S.stop.toFixed(2), t1: +S.t1.toFixed(2), t2: +S.t2.toFixed(2), score: d.score, news: d.news, tf: S.interval, strat: CFG.strategy });
        notify((d.action === "BUY" ? "🟢 BUY " : "🔴 SELL ") + S.symbol, "@" + S.entry.toFixed(2) + "  SL " + S.stop.toFixed(2) + "  T1 " + S.t1.toFixed(2) + "  T2 " + S.t2.toFixed(2) + "  · " + S.shares + " sh");
      }
    } else { checkExits(forming.h, forming.l); }
    const better = S.phase !== 0 && (d.action === "BUY" || d.action === "SELL") && d.score >= S.eScore + BETTER_MARGIN;
    const el = $("vim-banner");
    if (el && !(S.blocked && S.phase === 0)) {
      if (better) { el.textContent = "★ Stronger " + d.action + " setup forming (score " + d.score + ")"; el.className = "info show"; if (Date.now() - S.lastBetterAt > 60000) { S.lastBetterAt = Date.now(); notify("★ Stronger setup " + S.symbol, d.action + " score " + d.score); } }
      else el.className = "";
    }
  }

  // ---- render ----
  function renderStats() {
    const el = $("vim-stats"); if (!el) return;
    const n = STATS.trades, w = STATS.wins, l = STATS.losses, pr = n ? w / n : 0;
    const aw = w ? STATS.sumWinR / w : 0, al = l ? STATS.sumLossR / l : 0;
    const exp = n ? pr * aw - (1 - pr) * al : 0, payoff = al > 0 ? aw / al : 0, kelly = payoff > 0 ? pr - (1 - pr) / payoff : 0;
    const cls = (v) => v > 0 ? "pos" : v < 0 ? "neg" : "";
    el.innerHTML =
      `<div class="vim-st"><span>Day R</span><b class="${cls(STATS.cumR)}">${STATS.cumR.toFixed(2)}</b></div>` +
      `<div class="vim-st"><span>Trades</span><b>${n} (${w}/${l})</b></div>` +
      `<div class="vim-st"><span>Win%</span><b>${n ? Math.round(pr * 100) : "–"}</b></div>` +
      `<div class="vim-st"><span>Exp</span><b class="${cls(exp)}">${n ? exp.toFixed(2) + "R" : "–"}</b></div>` +
      `<div class="vim-st"><span>¼Kelly</span><b class="${cls(kelly)}">${n ? Math.round(Math.max(0, kelly) * 25) + "%" : "–"}</b></div>`;
  }
  function renderJournal() {
    const el = $("vim-journal"); if (!el) return;
    if (!JOURNAL.length) { el.innerHTML = '<div class="vim-je muted">No trades yet today.</div>'; return; }
    el.innerHTML = JOURNAL.slice(-8).reverse().map((j) => {
      const tm = new Date(j.t), hh = String(tm.getHours()).padStart(2, "0") + ":" + String(tm.getMinutes()).padStart(2, "0");
      return `<div class="vim-je"><span>${hh} ${j.side} ${j.sym}</span><b class="${j.R > 0 ? "pos" : j.R < 0 ? "neg" : ""}">${j.R > 0 ? "+" : ""}${j.R}R <i>${j.exit}</i></b></div>`;
    }).join("");
  }
  function exportCsv() {
    const rows = [["datetime", "symbol", "side", "entry", "stop", "exit", "R"]];
    JOURNAL.forEach((j) => rows.push([new Date(j.t).toISOString(), j.sym, j.side, j.entry, j.stop, j.exit, j.R]));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([rows.map((r) => r.join(",")).join("\n")], { type: "text/csv" }));
    a.download = "vim_journal_" + todayStr() + ".csv"; a.click();
  }
  function fchips(f) {
    if (!f) return "";
    return [["trend", "Trend"], ["vwap", "VWAP"], ["adx", "ADX"], ["mom", "Mom"], ["trigger", "Trigger"], ["news", "News"]]
      .map(([k, lbl]) => `<span class="vim-chip ${f[k] ? "on" : "off"}">${f[k] ? "✓" : "✗"} ${lbl}</span>`).join("");
  }
  function setDot() {
    const dot = $("vim-dot"); if (!dot) return;
    const age = Date.now() - S.lastOk;
    dot.className = !S.lastOk ? "off" : age < 15000 ? "" : age < 60000 ? "stale" : "off";
    dot.title = !S.lastOk ? "no data" : age < 15000 ? "live" : "stale (" + Math.round(age / 1000) + "s)";
  }
  function render(candles, d) {
    $("vim-sym").textContent = S.symbol || "—";
    $("vim-price").textContent = S.price ? "₹" + S.price.toFixed(2) : "₹—";
    setDot();
    const bn = $("vim-banner");
    if (S.blocked && S.phase === 0 && bn) { bn.textContent = "🛑 Daily limit — no new trades (" + STATS.trades + "/" + CFG.maxTrades + ", " + STATS.cumR.toFixed(1) + "R)"; bn.className = "warn show"; }
    const act = $("vim-action");
    act.textContent = S.phase === 1 ? "HOLDING LONG" : S.phase === -1 ? "HOLDING SHORT" : d.action === "BUY" ? "BUY NOW" : d.action === "SELL" ? "SELL NOW" : d.action === "HOLD" ? "HOLD" : "WAIT";
    act.className = S.phase === 1 ? "LONG" : S.phase === -1 ? "SHORT" : (d.action === "BUY" ? "BUY" : d.action === "SELL" ? "SELL" : "WAIT");
    const sv = d.score != null ? d.score : 0;
    $("vim-scoretext").textContent = sv;
    const fill = $("vim-scorefill"); fill.style.width = Math.min(100, sv) + "%"; fill.className = sv >= (CFG.minScore || 60) ? "hi" : sv >= 40 ? "mid" : "";
    $("vim-reason").textContent = S.phase !== 0 ? ("In trade @ " + S.entry.toFixed(2) + " · " + S.shares + " sh" + (S.t1done ? " · +1/3 booked, stop at breakeven" : "")) : (d.note || ("trend " + (d.trend || "?")));
    $("vim-factors").innerHTML = (S.phase === 0 && d.factors) ? fchips(d.factors) : "";
    const row = (k, v, c) => `<div class="vim-lv ${c}"><div class="k">${k}</div><div class="v">₹${Number(v).toFixed(2)}</div></div>`;
    const lv = $("vim-levels");
    lv.innerHTML = S.phase !== 0 ? [row("Entry", S.entry, "en"), row("SL", S.stop, "sl"), row("T1", S.t1, "t1"), row("T2", S.t2, "t2")].join("")
      : (d.levels ? [row("Entry", d.levels.entry, "en"), row("SL", d.levels.stop, "sl"), row("T1", d.levels.target1, "t1"), row("T2", d.levels.target2, "t2")].join("") : "");
    const nx = d.news ? ` · News <b class="${d.news === "bullish" ? "pos" : d.news === "bearish" ? "neg" : ""}">${d.news}${(d.warn || d.eventRisk) ? " ⚠" : ""}</b>` : "";
    $("vim-meta").innerHTML = `Trend <b>${d.regime || d.trend || "?"}</b> · ADX <b>${d.adx != null ? d.adx : "?"}</b> · RSI <b>${d.rsi != null ? d.rsi : "?"}</b> · Size <b>${S.shares}</b> sh${nx}`;
    ensureChart();
    if (S.series && candles.length) { S.series.setData(candles.map((c) => ({ time: c.t + IST, open: c.o, high: c.h, low: c.l, close: c.c }))); drawLevels(); S.chart.timeScale().fitContent(); }
  }

  async function refresh() {
    ensurePanel(); rolloverIfNewDay(); setDot();
    if (!S.url) { setDot(); return; }
    if (!S.news.sentiment && S.symbol) fetchNews();
    try {
      const candles = await fetchCandles(S.interval);
      if (!candles.length) return;
      S.lastOk = Date.now();
      const forming = candles[candles.length - 1];
      const closed = candles.length > 1 ? candles.slice(0, -1) : candles;  // signal on CLOSED bars
      S.price = forming.c;
      let d;
      if (CFG.strategy === "scanner") {
        const htfInt = S.interval < 15 ? 15 : S.interval * 4;
        let htf = "FLAT"; try { htf = Algos.htfTrendOf(await fetchCandles(htfInt)); } catch (e) {}
        d = Algos.swingDecide(closed, { minScore: 70, htfTrend: htf });
      } else if (CFG.strategy === "vim") {
        d = Algos.vimDecide(closed, { interval: S.interval, orMinutes: CFG.orMinutes, equity: CFG.equity, riskPct: CFG.riskPct, minADX: CFG.minADX, stopATR: CFG.stopATR, r1: CFG.r1, r2: CFG.r2 });
      } else {
        d = Algos.signalDecide(closed, { minScore: CFG.minScore, stopATR: CFG.stopATR, r1: CFG.r1, r2: CFG.r2, news: S.news });
      }
      if (d.levels) { const risk = Math.abs(d.levels.entry - d.levels.stop) || 1; S.shares = d.shares || Math.floor((CFG.riskPct / 100 * CFG.equity) / risk); }
      else S.shares = 0;
      machine(forming, d);
      render(candles, d);
    } catch (err) { S.lastOk = 0; setDot(); }
  }

  function init() {
    loadCfg(() => loadStats(() => {
      document.addEventListener("mousemove", onDocMove);
      document.addEventListener("mouseup", onDocUp);
      window.addEventListener("resize", resizeChart);
      build();
      setInterval(refresh, 4000);
      setInterval(ensurePanel, 3000);
      setInterval(setDot, 2000);
      setInterval(syncJournal, 120000);
      setTimeout(refresh, 1500);
    }));
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
