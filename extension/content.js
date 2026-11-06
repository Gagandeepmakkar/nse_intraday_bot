// NSE Groww Live — v1.0 live trade assistant.
// Reads Groww's OWN real-time candles (captured by inject_hook.js), runs the scored
// strategy, and walks ONE trade through its life with a desktop notification + sound
// at each step: WAIT -> BUY/SELL NOW -> HOLDING (breakeven after T1) -> EXIT (T1/T2/SL).
// Flags a "stronger setup" mid-trade. Shows a mini live chart with the levels drawn.
// Confirmation only; paper-trade first. Data rides your Groww session (no token stored).
(function () {
  "use strict";
  if (window.__nlcLoaded) return;
  window.__nlcLoaded = true;

  const IST = 19800;
  const BETTER_MARGIN = 10;
  const S = {
    url: "", auth: "", symbol: "",
    phase: 0, entry: 0, stop: 0, t1: 0, t2: 0, t1done: false, eScore: 0,
    chart: null, series: null, lines: [], lastBetterAt: 0
  };

  window.addEventListener("message", (e) => {
    const d = e.data;
    if (d && d.__nlcGroww && d.url && d.url.indexOf("charting_service") !== -1) {
      S.url = d.url;
      if (d.auth) S.auth = d.auth;
      const m = d.url.match(/\/CASH\/([^/]+)\/daily/);
      if (m && m[1] !== S.symbol) { S.symbol = m[1]; reset(); }  // new stock -> reset trade
    }
  });

  function reset() { S.phase = 0; S.t1done = false; S.eScore = 0; }

  // ---- notifications + sound ----
  function notify(title, body) {
    try { if (window.Notification && Notification.permission === "granted") new Notification(title, { body, silent: false }); } catch (e) {}
    beep();
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
  function build() {
    const p = document.createElement("div");
    p.id = "nse-live-confirm";
    p.innerHTML =
      '<div id="nlc-head"><span class="t">Groww Live ⚡</span><span class="s" id="nlc-status">…</span><span id="nlc-min" title="minimize">—</span></div>' +
      '<div id="nlc-body">' +
      '<div id="nlc-state"><span id="nlc-action" class="WAIT">—</span><span id="nlc-score">—</span></div>' +
      '<button id="nlc-alert">🔔 Enable alerts</button>' +
      '<div id="nlc-sub"></div>' +
      '<div id="nlc-better"></div>' +
      '<div id="nlc-levels"></div>' +
      '<div id="nlc-chart"></div>' +
      '<div id="nlc-ev"></div>' +
      '<div id="nlc-foot">Real-time via your Groww session · scored strategy · NOT advice — execute manually.</div>' +
      '</div>';
    document.body.appendChild(p);
    $("nlc-min").onclick = () => p.classList.toggle("min");
    const ab = $("nlc-alert");
    if (window.Notification && Notification.permission === "granted") ab.style.display = "none";
    ab.onclick = () => { try { Notification.requestPermission().then((r) => { if (r === "granted") { ab.style.display = "none"; notify("Alerts on", "You'll be pinged on BUY/SELL/targets."); } }); } catch (e) {} };
    const h = $("nlc-head"); let sx, sy, ox, oy, drag = false;
    h.addEventListener("mousedown", (e) => { if (e.target.id === "nlc-min") return; drag = true; sx = e.clientX; sy = e.clientY; const r = p.getBoundingClientRect(); ox = r.left; oy = r.top; e.preventDefault(); });
    document.addEventListener("mousemove", (e) => { if (!drag) return; p.style.left = (ox + e.clientX - sx) + "px"; p.style.top = (oy + e.clientY - sy) + "px"; p.style.right = "auto"; });
    document.addEventListener("mouseup", () => { drag = false; });
    S.chart = null;  // chart belongs to the old (removed) node
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

  async function fetchCandles() {
    let url = S.url.replace("minimal=true", "minimal=false");
    if (url.indexOf("minimal=") === -1) url += (url.indexOf("?") === -1 ? "?" : "&") + "minimal=false";
    const headers = { "Accept": "application/json" };
    if (S.auth) { headers["Authorization"] = S.auth; headers["X-APP-ID"] = "growwWeb"; headers["x-platform"] = "web"; }
    const r = await fetch(url, { headers, credentials: "include" });
    if (!r.ok) throw new Error("Groww " + r.status);
    const j = await r.json();
    return (j.candles || []).map((a) => ({ t: a[0], o: a[1], h: a[2], l: a[3], c: a[4], v: a[5] }));
  }

  // One-trade state machine on live candles.
  function machine(candles, d) {
    const last = candles[candles.length - 1];
    const hi = last.h, lo = last.l;
    if (S.phase === 0) {
      if (d.levels && (d.action === "BUY" || d.action === "SELL")) {
        S.phase = d.action === "BUY" ? 1 : -1;
        S.entry = d.levels.entry; S.stop = d.levels.stop; S.t1 = d.levels.target1; S.t2 = d.levels.target2;
        S.t1done = false; S.eScore = d.score;
        notify((d.action === "BUY" ? "🟢 BUY " : "🔴 SELL ") + S.symbol, "@" + S.entry.toFixed(2) + "  SL " + S.stop.toFixed(2) + "  T1 " + S.t1.toFixed(2) + "  T2 " + S.t2.toFixed(2) + "  (score " + S.score + ")");
      }
    } else if (S.phase === 1) {
      if (lo <= S.stop) { notify("⛔ STOP " + S.symbol, "Exit — stop hit"); reset(); }
      else {
        if (!S.t1done && hi >= S.t1) { S.t1done = true; S.stop = S.entry; notify("🎯 T1 " + S.symbol, "Book half, stop to breakeven"); }
        if (hi >= S.t2) { notify("✅ T2 " + S.symbol, "Exit the rest — target 2 hit"); reset(); }
      }
    } else if (S.phase === -1) {
      if (hi >= S.stop) { notify("⛔ STOP " + S.symbol, "Exit — stop hit"); reset(); }
      else {
        if (!S.t1done && lo <= S.t1) { S.t1done = true; S.stop = S.entry; notify("🎯 T1 " + S.symbol, "Book half, stop to breakeven"); }
        if (lo <= S.t2) { notify("✅ T2 " + S.symbol, "Exit the rest — target 2 hit"); reset(); }
      }
    }
    // stronger-setup flag while in a trade
    const better = S.phase !== 0 && (d.action === "BUY" || d.action === "SELL") && d.score >= S.eScore + BETTER_MARGIN;
    const el = $("nlc-better");
    if (el) {
      if (better) { el.textContent = "★ Stronger setup: " + d.action + " score " + d.score + " (open " + S.eScore + ") — consider switching"; el.classList.add("show"); if (Date.now() - S.lastBetterAt > 60000) { S.lastBetterAt = Date.now(); notify("★ Stronger setup " + S.symbol, d.action + " score " + d.score + " > open " + S.eScore); } }
      else el.classList.remove("show");
    }
  }

  function render(candles, d) {
    const act = $("nlc-action");
    const label = S.phase === 1 ? "HOLDING LONG" : S.phase === -1 ? "HOLDING SHORT" : d.action === "BUY" ? "BUY NOW" : d.action === "SELL" ? "SELL NOW" : d.action === "HOLD" ? "HOLD" : "WAIT";
    act.className = S.phase === 1 ? "LONG" : S.phase === -1 ? "SHORT" : d.action || "WAIT";
    act.textContent = label;
    const sc = $("nlc-score"); sc.textContent = "score " + (d.score != null ? d.score : "–"); sc.className = (d.score >= 70 ? "hi" : "");
    $("nlc-sub").textContent = S.phase !== 0 ? ("entered @ " + S.entry.toFixed(2) + " · score was " + S.eScore + (S.t1done ? " · T1 booked, stop at breakeven" : ""))
      : d.action === "HOLD" ? ("in trend — waiting for score ≥ 70 + ADX ≥ 20") : ("no clean setup · trend " + d.trend);
    $("nlc-levels").innerHTML = S.phase !== 0 ? [
      ["Entry", S.entry, "en"], ["SL", S.stop, "sl"], ["T1", S.t1, "t1"], ["T2", S.t2, "t2"]
    ].map(([k, v, c]) => `<div class="nlc-lv ${c}"><div class="k">${k}</div><div class="v">₹${Number(v).toFixed(2)}</div></div>`).join("")
      : (d.levels ? [["Entry", d.levels.entry, "en"], ["SL", d.levels.stop, "sl"], ["T1", d.levels.target1, "t1"], ["T2", d.levels.target2, "t2"]]
        .map(([k, v, c]) => `<div class="nlc-lv ${c}"><div class="k">${k}</div><div class="v">₹${Number(v).toFixed(2)}</div></div>`).join("")
        : `<div class="nlc-lv" style="grid-column:1/-1"><div class="k">No trade</div><div class="v">₹${d.price != null ? d.price : "?"}</div></div>`);
    $("nlc-ev").innerHTML = `<b>BUY</b> ${d.longScore != null ? d.longScore : "?"} · <b>SELL</b> ${d.shortScore != null ? d.shortScore : "?"} · ADX <b>${d.adx != null ? d.adx : "?"}</b> · RVOL ${d.rvol != null ? d.rvol : "?"}x · RSI ${d.rsi != null ? d.rsi : "?"}`;
    // chart
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
    if (!S.url) { const s = $("nlc-status"); if (s) s.textContent = "open a Groww stock chart…"; return; }
    try {
      const candles = await fetchCandles();
      if (!candles.length) { $("nlc-status").textContent = "no live data…"; return; }
      const d = Algos.swingDecide(candles);
      S.score = d.score;
      machine(candles, d);
      render(candles, d);
    } catch (err) { const s = $("nlc-status"); if (s) s.textContent = "err: " + err.message; }
  }

  function init() {
    build();
    setInterval(refresh, 10000);
    setInterval(ensurePanel, 3000);
    setTimeout(refresh, 2500);
    window.addEventListener("resize", () => { if (S.chart && $("nlc-chart")) S.chart.applyOptions({ width: $("nlc-chart").clientWidth }); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
