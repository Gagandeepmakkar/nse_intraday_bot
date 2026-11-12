// VIM Live — Groww trade assistant (content script).
// Reads Groww's OWN real-time candles (URL captured by inject_hook.js) + the on-page live
// price, runs the deterministic VIM/Scanner engine, walks ONE trade (WAIT -> BUY/SELL ->
// HOLDING -> EXIT), records every signal + graded outcome, and shows live stats.
// Confirmation only; paper-trade first. Everything runs locally in your browser.
(function () {
  "use strict";
  if (window.__nlcLoaded) return;
  window.__nlcLoaded = true;

  const IST = 19800;
  const BETTER_MARGIN = 10;
  const PRICE_BAND = 0.08;   // reject a live-price DOM read that deviates >8% from last close

  const S = {
    url: "", auth: "", symbol: "", interval: 5,
    phase: 0, entry: 0, stop: 0, t1: 0, t2: 0, t1done: false, eScore: 0,
    chart: null, series: null, lines: [], ro: null, panel: null, lastBetterAt: 0,
    shares: 0, score: 0, livePrice: 0, lastClose: 0,
    priceSel: "", priceEl: null, entry0: 0, stop0: 0, blocked: false, news: {}
  };

  // ---- Persisted config + UI geometry (chrome.storage.local) ----
  const DEFAULTS = {
    strategy: "signals", minScore: 60,
    interval: 5, orMinutes: 20, minADX: 20, stopATR: 1.5, r1: 1.5, r2: 3.0,
    equity: 100000, riskPct: 0.5, sound: true, alerts: true, maxTrades: 3, maxLossR: 2.0,
    ntfyTopic: "", tgToken: "", tgChat: ""
  };
  let CFG = Object.assign({}, DEFAULTS);
  let UI = null;   // { left, top, w, h }
  function loadCfg(cb) {
    try {
      chrome.storage.local.get(["cfg", "ui"], (r) => {
        CFG = Object.assign({}, DEFAULTS, r.cfg || {});
        UI = r.ui || null;
        S.interval = CFG.interval;
        if (cb) cb();
      });
    } catch (e) { if (cb) cb(); }
  }
  function saveCfg() { try { chrome.storage.local.set({ cfg: CFG }); } catch (e) {} }
  function saveGeom() {
    if (!S.panel) return;
    const r = S.panel.getBoundingClientRect();
    UI = { left: r.left, top: r.top, w: r.width, h: r.height };
    try { chrome.storage.local.set({ ui: UI }); } catch (e) {}
  }

  // ---- Session stats (reset daily) + trade journal, both persisted ----
  function todayStr() { const d = new Date(); return d.getFullYear() + "-" + (d.getMonth() + 1) + "-" + d.getDate(); }
  function freshStats() { return { day: todayStr(), trades: 0, wins: 0, losses: 0, cumR: 0, sumWinR: 0, sumLossR: 0 }; }
  let STATS = freshStats();
  let JOURNAL = [];
  function loadStats(cb) {
    try {
      chrome.storage.local.get(["stats", "journal"], (r) => {
        STATS = (r.stats && r.stats.day === todayStr()) ? r.stats : freshStats();
        JOURNAL = r.journal || [];
        if (cb) cb();
      });
    } catch (e) { if (cb) cb(); }
  }
  function saveStats() { try { chrome.storage.local.set({ stats: STATS, journal: JOURNAL.slice(-500) }); } catch (e) {} }
  function rolloverIfNewDay() {
    if (STATS.day !== todayStr()) { STATS = freshStats(); saveStats(); renderStats(); }
  }

  try {
    chrome.storage.onChanged.addListener((ch, area) => {
      if (area !== "local") return;
      if (ch.cfg) { CFG = Object.assign({}, DEFAULTS, ch.cfg.newValue || {}); S.interval = CFG.interval; syncSettingsUI(); }
      if (ch.stats && ch.stats.newValue) { STATS = ch.stats.newValue; renderStats(); }
      if (ch.journal) { JOURNAL = ch.journal.newValue || []; }
    });
  } catch (e) {}

  // ---- Symbol / URL capture from the MAIN-world hook ----
  window.addEventListener("message", (e) => {
    const d = e.data;
    if (!(d && d.__nlcGroww && d.url && d.url.indexOf("charting_service") !== -1)) return;
    S.url = d.url;
    if (d.auth) S.auth = d.auth;
    const m = d.url.match(/\/CASH\/([^/]+)\/daily/);
    if (m && m[1] !== S.symbol) {
      // Switching stocks: resolve any open trade (so the journal never has an orphan
      // signal), then clear the price cache so we don't read the old symbol's price.
      if (S.phase !== 0) closeTrade(floatingR(), "SWITCH");
      S.symbol = m[1];
      S.priceEl = null; S.priceSel = ""; S.lastClose = 0; S.livePrice = 0; S.news = {};
      reset();
      fetchNews();
    }
  });

  // News verdict for the current symbol (from the local server; graceful if it's off).
  function fetchNews() {
    if (!S.symbol) return;
    const sym = S.symbol;
    try {
      chrome.runtime.sendMessage({ __news: true, symbol: sym }, (r) => {
        if (r && sym === S.symbol) S.news = r;   // ignore if user already switched again
      });
    } catch (e) {}
  }

  function reset() {
    S.phase = 0; S.t1done = false; S.eScore = 0;
    S.entry = S.stop = S.t1 = S.t2 = S.entry0 = S.stop0 = 0;
  }
  // Current R of the open trade, from the live price (for switch/abandon accounting).
  function floatingR() {
    if (S.phase === 0 || !S.entry0) return 0;
    const px = S.livePrice || S.lastClose || S.entry0;
    const risk = Math.abs(S.entry0 - S.stop0) || 1;
    const raw = S.phase === 1 ? (px - S.entry0) / risk : (S.entry0 - px) / risk;
    return +(S.t1done ? (CFG.r1 / 3 + (2 / 3) * raw) : raw).toFixed(2);
  }

  // ---- notifications ----
  function notify(title, body) {
    if (!CFG.alerts) return;
    try { if (window.Notification && Notification.permission === "granted") new Notification(title, { body, silent: !CFG.sound }); } catch (e) {}
    if (CFG.sound) beep();
    telegram(title + " — " + body);
    ntfy(title, body);
  }
  function ntfy(title, body) {
    if (!CFG.ntfyTopic) return;
    try { chrome.runtime.sendMessage({ __ntfy: true, topic: CFG.ntfyTopic, title, text: body }); } catch (e) {}
  }
  function telegram(text) {
    if (!CFG.tgToken || !CFG.tgChat) return;
    try { chrome.runtime.sendMessage({ __tg: true, token: CFG.tgToken, chat: CFG.tgChat, text }); } catch (e) {}
  }
  function logEvent(ev) { try { chrome.runtime.sendMessage({ __log: true, events: [ev] }); } catch (e) {} }
  function syncJournal() {
    if (!JOURNAL.length) return;
    try { chrome.runtime.sendMessage({ __log: true, events: JOURNAL.map(j => Object.assign({ event: "close" }, j)) }); } catch (e) {}
  }
  function beep() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.connect(g); g.connect(ctx.destination); o.type = "sine"; o.frequency.value = 880; g.gain.value = 0.08;
      o.start(); setTimeout(() => { o.stop(); ctx.close(); }, 180);
    } catch (e) {}
  }

  const $ = (id) => document.getElementById(id);
  const num = (id, label, val, step) =>
    '<label class="nlc-f"><span>' + label + '</span><input id="' + id + '" type="number" step="' + step + '" value="' + val + '"></label>';

  // ---- Drag + resize (document handlers attached ONCE in init; no per-build leak) ----
  let DRAG = null, RZ = null;
  function onDocMove(e) {
    const p = S.panel; if (!p) return;
    if (DRAG) {
      p.style.left = (DRAG.ox + e.clientX - DRAG.sx) + "px";
      p.style.top = (DRAG.oy + e.clientY - DRAG.sy) + "px";
      p.style.right = "auto";
    } else if (RZ) {
      p.style.width = Math.max(300, Math.min(window.innerWidth * 0.96, RZ.w + e.clientX - RZ.sx)) + "px";
      p.style.height = Math.max(150, Math.min(window.innerHeight * 0.92, RZ.h + e.clientY - RZ.sy)) + "px";
      resizeChart();
    }
  }
  function onDocUp() { if (DRAG || RZ) saveGeom(); DRAG = null; RZ = null; }
  function resizeChart() { const dv = $("nlc-chart"); if (S.chart && dv) S.chart.applyOptions({ width: dv.clientWidth }); }

  function build() {
    const p = document.createElement("div");
    p.id = "nse-live-confirm";
    p.innerHTML =
      '<div id="nlc-head"><span class="t">VIM Live ⚡</span><span class="s" id="nlc-live" title="live price (1s)">₹–</span><span class="s" id="nlc-status">…</span><span id="nlc-gear" title="settings">⚙</span><span id="nlc-min" title="minimize">—</span></div>' +
      '<div id="nlc-body">' +
      '<div id="nlc-banner"></div>' +
      '<div id="nlc-state"><span id="nlc-action" class="WAIT">—</span><span id="nlc-score">—</span></div>' +
      '<div id="nlc-tfrow">TF: <select id="nlc-tf"><option value="3">3m</option><option value="5">5m</option><option value="15">15m</option></select>' +
      ' · <select id="nlc-strat"><option value="signals">Signals</option><option value="vim">VIM</option><option value="scanner">Scanner</option></select> <span id="nlc-htf"></span></div>' +
      '<button id="nlc-alert">🔔 Enable alerts</button>' +
      '<div id="nlc-sub"></div>' +
      '<div id="nlc-better"></div>' +
      '<div id="nlc-levels"></div>' +
      '<div id="nlc-chart"></div>' +
      '<div id="nlc-ev"></div>' +
      '<div id="nlc-stats"></div>' +
      '<div id="nlc-jrow"><span id="nlc-jtitle">Journal</span><button id="nlc-exp" class="nlc-mini">Export CSV</button><button id="nlc-clr" class="nlc-mini">Reset day</button></div>' +
      '<div id="nlc-journal"></div>' +
      '<div id="nlc-settings" class="hidden">' +
        '<div class="nlc-sgrid">' +
          num("set-equity", "Capital ₹", CFG.equity, "1000") + num("set-risk", "Risk %", CFG.riskPct, "0.1") +
          num("set-or", "OR min", CFG.orMinutes, "5") + num("set-adx", "Min ADX", CFG.minADX, "1") +
          num("set-stop", "Stop ATR", CFG.stopATR, "0.1") + num("set-r1", "Target1 R", CFG.r1, "0.5") +
          num("set-r2", "Target2 R", CFG.r2, "0.5") + num("set-score", "Min signal score", CFG.minScore, "5") +
          num("set-maxt", "Max trades/day", CFG.maxTrades, "1") + num("set-maxl", "Daily stop (−R)", CFG.maxLossR, "0.5") +
        '</div>' +
        '<label class="nlc-chk"><input type="checkbox" id="set-sound"> Sound</label>' +
        '<label class="nlc-chk"><input type="checkbox" id="set-alerts"> Desktop alerts</label>' +
        '<div class="nlc-tg">📱 Phone push — ntfy (easiest, no login):' +
          '<input id="set-ntfy" placeholder="your secret topic e.g. gagan-vim-9f3k2">' +
          '<button id="set-ntfytest" class="nlc-mini">Test phone</button></div>' +
        '<div class="nlc-tg">Telegram alerts (optional):' +
          '<input id="set-tgtoken" placeholder="bot token"><input id="set-tgchat" placeholder="chat id">' +
          '<button id="set-tgtest" class="nlc-mini">Test</button></div>' +
        '<button id="set-save" class="nlc-save">Save settings</button>' +
      '</div>' +
      '<div id="nlc-foot">Real-time via your Groww session · paper-trade · NOT advice — execute manually.</div>' +
      '</div>' +
      '<div id="nlc-resize" title="drag to resize">⤡</div>';
    document.body.appendChild(p);
    S.panel = p;
    if (UI) {   // restore saved position/size
      if (UI.left != null) { p.style.left = UI.left + "px"; p.style.top = UI.top + "px"; p.style.right = "auto"; }
      if (UI.w) { p.style.width = UI.w + "px"; p.style.height = UI.h + "px"; }
    }
    if (S.ro) { try { S.ro.disconnect(); } catch (e) {} }
    try { S.ro = new ResizeObserver(resizeChart); S.ro.observe(p); } catch (e) {}

    $("nlc-resize").addEventListener("mousedown", (e) => {
      const r = p.getBoundingClientRect(); RZ = { sx: e.clientX, sy: e.clientY, w: r.width, h: r.height };
      e.preventDefault(); e.stopPropagation();
    });
    $("nlc-head").addEventListener("mousedown", (e) => {
      if (e.target.id === "nlc-min" || e.target.id === "nlc-gear") return;
      const r = p.getBoundingClientRect(); DRAG = { sx: e.clientX, sy: e.clientY, ox: r.left, oy: r.top };
      e.preventDefault();
    });
    $("nlc-min").onclick = () => p.classList.toggle("min");
    $("nlc-gear").onclick = () => $("nlc-settings").classList.toggle("hidden");

    const tf = $("nlc-tf"); tf.value = String(S.interval);
    tf.onchange = () => { CFG.interval = S.interval = parseInt(tf.value, 10) || 5; saveCfg(); reset(); refresh(); };
    const strat = $("nlc-strat"); strat.value = CFG.strategy;
    strat.onchange = () => { CFG.strategy = strat.value; saveCfg(); reset(); refresh(); };
    const ab = $("nlc-alert");
    if (window.Notification && Notification.permission === "granted") ab.style.display = "none";
    ab.onclick = () => { try { Notification.requestPermission().then((r) => { if (r === "granted") { ab.style.display = "none"; notify("Alerts on", "You'll be pinged on BUY/SELL/targets."); } }); } catch (e) {} };
    $("nlc-exp").onclick = exportCsv;
    $("nlc-clr").onclick = () => { if (confirm("Reset today's stats and journal?")) { STATS = freshStats(); JOURNAL = []; saveStats(); renderStats(); renderJournal(); } };
    $("set-save").onclick = readSettingsUI;
    $("set-ntfytest").onclick = () => {
      CFG.ntfyTopic = $("set-ntfy").value.trim();
      if (!CFG.ntfyTopic) { $("nlc-status").textContent = "enter a topic name"; return; }
      saveCfg();
      try { chrome.runtime.sendMessage({ __ntfy: true, topic: CFG.ntfyTopic, title: "VIM test", text: "✅ Phone alerts work — subscribe to this topic in the ntfy app." }, (r) => { $("nlc-status").textContent = (r && r.ok) ? "ntfy ✓ sent — check your phone" : "ntfy failed"; }); } catch (e) {}
    };
    $("set-tgtest").onclick = () => {
      CFG.tgToken = $("set-tgtoken").value.trim(); CFG.tgChat = $("set-tgchat").value.trim();
      if (!CFG.tgToken) { $("nlc-status").textContent = "paste the bot token"; return; }
      const report = (r) => { $("nlc-status").textContent = (r && r.ok) ? "Telegram ✓ delivered" : "Telegram FAILED: " + ((r && r.desc) || "start the bot / chat id"); };
      if (CFG.tgChat) { saveCfg(); try { chrome.runtime.sendMessage({ __tg: true, token: CFG.tgToken, chat: CFG.tgChat, text: "✅ VIM test alert — Telegram connected." }, report); } catch (e) {} return; }
      $("nlc-status").textContent = "connecting Telegram…";
      try {
        chrome.runtime.sendMessage({ __tgConnect: true, token: CFG.tgToken }, (r) => {
          if (r && r.ok && r.chat) { CFG.tgChat = r.chat; $("set-tgchat").value = r.chat; saveCfg(); telegram("✅ VIM test alert — Telegram connected."); $("nlc-status").textContent = "Telegram ✓ connected"; }
          else $("nlc-status").textContent = "press Start in your bot, then retry";
        });
      } catch (e) {}
    };
    syncSettingsUI();
    S.chart = null;   // the old chart belonged to the removed node
    renderStats(); renderJournal();
  }

  function syncSettingsUI() {
    if (!$("set-equity")) return;
    $("set-equity").value = CFG.equity; $("set-risk").value = CFG.riskPct; $("set-or").value = CFG.orMinutes;
    $("set-adx").value = CFG.minADX; $("set-stop").value = CFG.stopATR; $("set-r1").value = CFG.r1; $("set-r2").value = CFG.r2;
    $("set-score").value = CFG.minScore; $("set-maxt").value = CFG.maxTrades; $("set-maxl").value = CFG.maxLossR;
    $("set-sound").checked = CFG.sound; $("set-alerts").checked = CFG.alerts;
    $("set-ntfy").value = CFG.ntfyTopic; $("set-tgtoken").value = CFG.tgToken; $("set-tgchat").value = CFG.tgChat;
    if ($("nlc-tf")) $("nlc-tf").value = String(CFG.interval);
    if ($("nlc-strat")) $("nlc-strat").value = CFG.strategy;
  }
  function readSettingsUI() {
    const n = (id, d) => { const v = parseFloat($(id).value); return isNaN(v) ? d : v; };
    CFG.equity = n("set-equity", CFG.equity); CFG.riskPct = n("set-risk", CFG.riskPct); CFG.orMinutes = n("set-or", CFG.orMinutes);
    CFG.minADX = n("set-adx", CFG.minADX); CFG.stopATR = n("set-stop", CFG.stopATR); CFG.r1 = n("set-r1", CFG.r1); CFG.r2 = n("set-r2", CFG.r2);
    CFG.minScore = n("set-score", CFG.minScore); CFG.maxTrades = n("set-maxt", CFG.maxTrades); CFG.maxLossR = n("set-maxl", CFG.maxLossR);
    CFG.sound = $("set-sound").checked; CFG.alerts = $("set-alerts").checked;
    CFG.ntfyTopic = $("set-ntfy").value.trim(); CFG.tgToken = $("set-tgtoken").value.trim(); CFG.tgChat = $("set-tgchat").value.trim();
    saveCfg(); $("nlc-settings").classList.add("hidden"); refresh();
  }
  function ensurePanel() { if (!document.getElementById("nse-live-confirm")) build(); }

  function ensureChart() {
    if (S.chart || !window.LightweightCharts) return;
    const div = $("nlc-chart"); if (!div) return;
    S.chart = LightweightCharts.createChart(div, {
      height: 180, width: div.clientWidth,
      layout: { background: { color: "transparent" }, textColor: "#8a94a2" },
      grid: { vertLines: { color: "#161d27" }, horzLines: { color: "#161d27" } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#1e2733" },
      rightPriceScale: { borderColor: "#1e2733" }
    });
    S.series = S.chart.addCandlestickSeries({ upColor: "#22d36f", downColor: "#ff5c6c", borderVisible: false, wickUpColor: "#22d36f", wickDownColor: "#ff5c6c" });
  }
  function drawLevels() {
    if (!S.series) return;
    S.lines.forEach((l) => S.series.removePriceLine(l)); S.lines = [];
    if (S.phase === 0) return;
    const add = (price, color, title) => { if (price) S.lines.push(S.series.createPriceLine({ price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title })); };
    add(S.entry, "#38bdf8", "Entry"); add(S.stop, "#ff5c6c", "SL"); add(S.t1, "#ffd23f", "T1"); add(S.t2, "#22d36f", "T2");
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

  // Real-time exit check against the fixed trade levels. Called by the 1s ticker (live
  // price) and by the bar refresh (closed-bar hi/lo as a wick safety net). reset()/t1done
  // guards make repeat calls no-ops. After T1 the stop moves to breakeven (no trailing).
  function checkExits(hi, lo) {
    if (S.phase === 1) {
      if (lo <= S.stop) { const R = S.t1done ? CFG.r1 / 3 : -1; notify((R > 0 ? "🟡 BE " : "⛔ STOP ") + S.symbol, R > 0 ? "Stopped at breakeven (+1/3 booked)" : "Exit — stop hit"); closeTrade(R, S.t1done ? "BE" : "SL"); }
      else {
        if (!S.t1done && hi >= S.t1) { S.t1done = true; S.stop = S.entry; notify("🎯 +1R " + S.symbol, "Booked 1/3; stop → breakeven, rest runs to T2"); }
        if (hi >= S.t2) { notify("✅ +" + CFG.r2 + "R " + S.symbol, "T2 hit — exit the rest"); closeTrade(CFG.r1 / 3 + (2 / 3) * CFG.r2, "T2"); }
      }
    } else if (S.phase === -1) {
      if (hi >= S.stop) { const R = S.t1done ? CFG.r1 / 3 : -1; notify((R > 0 ? "🟡 BE " : "⛔ STOP ") + S.symbol, R > 0 ? "Stopped at breakeven (+1/3 booked)" : "Exit — stop hit"); closeTrade(R, S.t1done ? "BE" : "SL"); }
      else {
        if (!S.t1done && lo <= S.t1) { S.t1done = true; S.stop = S.entry; notify("🎯 +1R " + S.symbol, "Booked 1/3; stop → breakeven, rest runs to T2"); }
        if (lo <= S.t2) { notify("✅ +" + CFG.r2 + "R " + S.symbol, "T2 hit — exit the rest"); closeTrade(CFG.r1 / 3 + (2 / 3) * CFG.r2, "T2"); }
      }
    }
  }

  function closeTrade(R, reason) {
    STATS.trades++; STATS.cumR += R;
    if (R > 0) { STATS.wins++; STATS.sumWinR += R; } else { STATS.losses++; STATS.sumLossR += -R; }
    const rec = { t: Date.now(), sym: S.symbol, side: S.phase === 1 ? "LONG" : "SHORT", entry: +S.entry0.toFixed(2), stop: +S.stop0.toFixed(2), exit: reason, R: +R.toFixed(2) };
    JOURNAL.push(rec);
    logEvent(Object.assign({ event: "close", tf: S.interval, strat: CFG.strategy }, rec));
    saveStats(); renderStats(); renderJournal();
    reset();
  }

  // Entry on CLOSED bars (whipsaw-free); exits in real time via checkExits().
  function machine(candles, d) {
    const last = candles[candles.length - 1];
    S.blocked = STATS.trades >= CFG.maxTrades || STATS.cumR <= -Math.abs(CFG.maxLossR);
    if (S.phase === 0) {
      if (!S.blocked && d.levels && (d.action === "BUY" || d.action === "SELL")) {
        S.phase = d.action === "BUY" ? 1 : -1;
        S.entry = d.levels.entry; S.stop = d.levels.stop; S.t1 = d.levels.target1; S.t2 = d.levels.target2;
        S.entry0 = S.entry; S.stop0 = S.stop; S.t1done = false; S.eScore = d.score;
        logEvent({ event: "signal", t: Date.now(), sym: S.symbol, side: d.action === "BUY" ? "LONG" : "SHORT", entry: +S.entry.toFixed(2), stop: +S.stop.toFixed(2), t1: +S.t1.toFixed(2), t2: +S.t2.toFixed(2), score: d.score, regime: d.regime, nearRes: d.nearRes, nearSup: d.nearSup, tf: S.interval, strat: CFG.strategy });
        notify((d.action === "BUY" ? "🟢 BUY " : "🔴 SELL ") + S.symbol, "@" + S.entry.toFixed(2) + "  SL " + S.stop.toFixed(2) + "  T1 " + S.t1.toFixed(2) + "  T2 " + S.t2.toFixed(2) + "  · " + S.shares + " sh");
      }
    } else {
      checkExits(last.h, last.l);
    }
    const better = S.phase !== 0 && (d.action === "BUY" || d.action === "SELL") && d.score >= S.eScore + BETTER_MARGIN;
    const el = $("nlc-better");
    if (el) {
      if (better) { el.textContent = "★ Stronger setup: " + d.action + " score " + d.score + " (open " + S.eScore + ")"; el.classList.add("show"); if (Date.now() - S.lastBetterAt > 60000) { S.lastBetterAt = Date.now(); notify("★ Stronger setup " + S.symbol, d.action + " score " + d.score + " > open " + S.eScore); } }
      else el.classList.remove("show");
    }
  }

  // ---- Live price off Groww's page (no network). Validated against the last close so a
  // stray DOM number can never fire a false stop/target. ----
  function parsePrice(el) {
    if (!el) return null;
    const t = (el.textContent || "").replace(/[₹,\s]/g, "");
    return /^\d+(\.\d{1,2})?$/.test(t) ? parseFloat(t) : null;
  }
  function readLivePrice() {
    if (S.priceEl && S.priceEl.isConnected) { const v = parsePrice(S.priceEl); if (v) return v; }
    if (S.priceSel) { const el = document.querySelector(S.priceSel); const v = parsePrice(el); if (v) { S.priceEl = el; return v; } }
    const cands = ['[data-testid*="ltp" i]', '[class*="ltp" i]', '[class*="lastPrice" i]', '[class*="livePrice" i]'];
    for (const sel of cands) {
      for (const el of document.querySelectorAll(sel)) { const v = parsePrice(el); if (v) { S.priceSel = sel; S.priceEl = el; return v; } }
    }
    let bestEl = null, best = null, bestSize = 0;
    const rx = /₹\s?[\d,]+\.\d{2}/;
    for (const el of document.querySelectorAll("span, div, h1, h2, p")) {
      if (el.children.length) continue;
      if (!rx.test(el.textContent || "")) continue;
      const r = el.getBoundingClientRect();
      if (r.top < 0 || r.top > window.innerHeight * 0.4 || r.width === 0) continue;
      const size = parseFloat(getComputedStyle(el).fontSize) || 0;
      if (size > bestSize) { const v = parsePrice(el); if (v) { best = v; bestSize = size; bestEl = el; } }
    }
    if (bestEl) S.priceEl = bestEl;
    return best;
  }

  function tick() {
    if (!S.panel) return;
    let p = readLivePrice();
    if (p && S.lastClose && Math.abs(p / S.lastClose - 1) > PRICE_BAND) p = null;  // reject bad read
    if (!p) return;
    S.livePrice = p;
    const pv = $("nlc-live"); if (pv) pv.textContent = "₹" + p.toFixed(2);
    if (S.phase !== 0) checkExits(p, p);
  }

  // ---- Stats / journal / CSV ----
  function renderStats() {
    const el = $("nlc-stats"); if (!el) return;
    const n = STATS.trades, w = STATS.wins, l = STATS.losses, pr = n ? w / n : 0;
    const aw = w ? STATS.sumWinR / w : 0, al = l ? STATS.sumLossR / l : 0;
    const exp = n ? pr * aw - (1 - pr) * al : 0;
    const payoff = al > 0 ? aw / al : 0;
    const kelly = payoff > 0 ? pr - (1 - pr) / payoff : 0;
    const cls = (v) => v > 0 ? "pos" : v < 0 ? "neg" : "";
    el.innerHTML =
      `<div class="nlc-st"><span>Day R</span><b class="${cls(STATS.cumR)}">${STATS.cumR.toFixed(2)}</b></div>` +
      `<div class="nlc-st"><span>Trades</span><b>${n} (${w}/${l})</b></div>` +
      `<div class="nlc-st"><span>Win%</span><b>${n ? Math.round(pr * 100) : "–"}</b></div>` +
      `<div class="nlc-st"><span>Expectancy</span><b class="${cls(exp)}">${n ? exp.toFixed(2) + "R" : "–"}</b></div>` +
      `<div class="nlc-st"><span>Kelly</span><b class="${cls(kelly)}">${n ? Math.round(Math.max(0, kelly) * 25) + "%" : "–"}</b></div>`;
  }
  function renderJournal() {
    const el = $("nlc-journal"); if (!el) return;
    if (!JOURNAL.length) { el.innerHTML = '<div class="nlc-je muted">No trades yet today.</div>'; return; }
    el.innerHTML = JOURNAL.slice(-6).reverse().map((j) => {
      const tm = new Date(j.t), hh = String(tm.getHours()).padStart(2, "0") + ":" + String(tm.getMinutes()).padStart(2, "0");
      return `<div class="nlc-je"><span>${hh} ${j.side} ${j.sym}</span><b class="${j.R > 0 ? "pos" : j.R < 0 ? "neg" : ""}">${j.R > 0 ? "+" : ""}${j.R}R <i>${j.exit}</i></b></div>`;
    }).join("");
  }
  function exportCsv() {
    const rows = [["datetime", "symbol", "side", "entry", "stop", "exit", "R"]];
    JOURNAL.forEach((j) => rows.push([new Date(j.t).toISOString(), j.sym, j.side, j.entry, j.stop, j.exit, j.R]));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([rows.map((r) => r.join(",")).join("\n")], { type: "text/csv" }));
    a.download = "vim_journal_" + todayStr() + ".csv"; a.click();
  }

  function render(candles, d) {
    const bn = $("nlc-banner");
    if (bn) { if (S.blocked && S.phase === 0) { bn.textContent = "🛑 Daily limit hit — no new trades (" + STATS.trades + "/" + CFG.maxTrades + ", " + STATS.cumR.toFixed(1) + "R). Reset to override."; bn.classList.add("show"); } else bn.classList.remove("show"); }
    const act = $("nlc-action");
    act.textContent = S.phase === 1 ? "HOLDING LONG" : S.phase === -1 ? "HOLDING SHORT" : d.action === "BUY" ? "BUY NOW" : d.action === "SELL" ? "SELL NOW" : d.action === "HOLD" ? "HOLD" : "WAIT";
    act.className = S.phase === 1 ? "LONG" : S.phase === -1 ? "SHORT" : d.action || "WAIT";
    const sc = $("nlc-score"); sc.textContent = "score " + (d.score != null ? d.score : "–"); sc.className = (d.score >= 70 ? "hi" : "");
    $("nlc-sub").textContent = S.phase !== 0
      ? ("entered @ " + S.entry.toFixed(2) + " · " + S.shares + " sh" + (S.t1done ? " · +1/3 booked, stop at breakeven" : ""))
      : (d.note || ("trend " + d.trend));
    const row = (k, v, c) => `<div class="nlc-lv ${c}"><div class="k">${k}</div><div class="v">₹${Number(v).toFixed(2)}</div></div>`;
    $("nlc-levels").innerHTML = S.phase !== 0
      ? [row("Entry", S.entry, "en"), row("SL", S.stop, "sl"), row("T1", S.t1, "t1"), row("T2", S.t2, "t2")].join("")
      : (d.levels ? [row("Entry", d.levels.entry, "en"), row("SL", d.levels.stop, "sl"), row("T1", d.levels.target1, "t1"), row("T2", d.levels.target2, "t2")].join("")
        : `<div class="nlc-lv" style="grid-column:1/-1"><div class="k">No trade</div><div class="v">₹${d.price != null ? d.price : "?"}</div></div>`);
    const sr = (d.nearSup != null || d.nearRes != null) ? ` · Sup <b>${d.nearSup != null ? "₹" + d.nearSup : "—"}</b> / Res <b>${d.nearRes != null ? "₹" + d.nearRes : "—"}</b>` : "";
    const nx = d.news ? ` · News <b class="${d.news === "bullish" ? "pos" : d.news === "bearish" ? "neg" : ""}">${d.news}${d.eventRisk ? " ⚠" : ""}</b>` : "";
    $("nlc-ev").innerHTML = `Trend <b>${d.regime || d.trend || "?"}</b> · ADX <b>${d.adx != null ? d.adx : "?"}</b> · RSI ${d.rsi != null ? d.rsi : "?"} · size <b>${S.shares}</b> sh${sr}${nx}`;
    ensureChart();
    if (S.series && candles.length) {
      S.series.setData(candles.map((c) => ({ time: c.t + IST, open: c.o, high: c.h, low: c.l, close: c.c })));
      drawLevels(); S.chart.timeScale().fitContent();
    }
    const now = new Date();
    $("nlc-status").textContent = S.symbol + " · " + String(now.getHours()).padStart(2, "0") + ":" + String(now.getMinutes()).padStart(2, "0");
  }

  async function refresh() {
    ensurePanel();
    rolloverIfNewDay();
    if (!S.url) { const s = $("nlc-status"); if (s) s.textContent = "open a Groww stock chart…"; return; }
    try {
      const candles = await fetchCandles(S.interval);
      if (!candles.length) { $("nlc-status").textContent = "no live data…"; return; }
      S.lastClose = candles[candles.length - 1].c;
      // Higher-timeframe trend is only used by the Scanner strategy; VIM has its own regime.
      // Skip the extra Groww fetch in VIM mode (fewer calls, no rate-limit risk).
      if (!S.news.sentiment && S.symbol) fetchNews();   // first run / page-load fetch
      let htfTrend = "FLAT";
      const hb = $("nlc-htf");
      if (CFG.strategy === "scanner") {
        const htfInt = S.interval < 15 ? 15 : S.interval * 4;
        try { htfTrend = Algos.htfTrendOf(await fetchCandles(htfInt)); } catch (e) {}
        if (hb) hb.textContent = "· " + htfInt + "m trend: " + htfTrend;
      } else if (hb) { hb.textContent = S.news.sentiment ? ("· news: " + S.news.sentiment) : ""; }
      let d;
      if (CFG.strategy === "scanner") d = Algos.swingDecide(candles, { minScore: 70, htfTrend });
      else if (CFG.strategy === "vim") d = Algos.vimDecide(candles, { interval: S.interval, orMinutes: CFG.orMinutes, equity: CFG.equity, riskPct: CFG.riskPct, minADX: CFG.minADX, stopATR: CFG.stopATR, r1: CFG.r1, r2: CFG.r2 });
      else d = Algos.signalDecide(candles, { minScore: CFG.minScore, stopATR: CFG.stopATR, r1: CFG.r1, r2: CFG.r2, news: S.news });
      // vol-targeted share size for whichever strategy
      if (d.levels && !d.shares) { const risk = Math.abs(d.levels.entry - d.levels.stop) || 1; d.shares = Math.floor((CFG.riskPct / 100 * CFG.equity) / risk); }
      S.score = d.score; S.shares = d.shares || 0;
      machine(candles, d);
      render(candles, d);
    } catch (err) { const s = $("nlc-status"); if (s) s.textContent = "err: " + err.message; }
  }

  function init() {
    loadCfg(() => loadStats(() => {
      document.addEventListener("mousemove", onDocMove);
      document.addEventListener("mouseup", onDocUp);
      window.addEventListener("resize", resizeChart);
      build();
      setInterval(refresh, 5000);
      setInterval(tick, 1000);
      setInterval(ensurePanel, 3000);
      setInterval(syncJournal, 120000);
      setTimeout(refresh, 2500);
    }));
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
