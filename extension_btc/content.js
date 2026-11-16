// BTC/ETH Donchian trend panel on TradingView. Reads free Binance data via engine.js,
// shows the current trend position (LONG/SHORT/FLAT) + exact levels for BTC & ETH, 24/7.
(function () {
  "use strict";
  if (window.__btcTrendLoaded) return;
  window.__btcTrendLoaded = true;
  const SYMS = ["BTC", "ETH"];   // data is fetched by the background worker (handles CORS/geo)
  const DEFAULTS = { interval: "1d", period: 20, allowShort: true, ntfyTopic: "" };
  let CFG = Object.assign({}, DEFAULTS);
  let lastStates = null;
  const CHART = { obj: null, series: null, lines: [], sym: "BTC" };
  const $ = (id) => document.getElementById(id);

  function loadCfg(cb) { try { chrome.storage.local.get(["btccfg"], (r) => { CFG = Object.assign({}, DEFAULTS, r.btccfg || {}); cb && cb(); }); } catch (e) { cb && cb(); } }
  function saveCfg() { try { chrome.storage.local.set({ btccfg: CFG }); } catch (e) {} }

  function build() {
    const p = document.createElement("div");
    p.id = "btc-panel";
    p.innerHTML =
      '<div id="btc-head"><span class="t">₿ Trend</span><span id="btc-mode"></span><span id="btc-gear" class="ic" title="settings">⚙</span><span id="btc-min" class="ic">–</span></div>' +
      '<div id="btc-body">' +
      '<div id="btc-rows"></div>' +
      '<div id="btc-chart-tabs"><span class="btc-tab active" data-s="BTC">BTC</span><span class="btc-tab" data-s="ETH">ETH</span></div>' +
      '<div id="btc-chart"></div>' +
      '<div id="btc-stats"></div>' +
      '<div id="btc-journal"></div>' +
      '<div id="btc-set" class="hidden">' +
        '<label class="btc-f"><span>Timeframe</span><select id="btc-int"><option value="1d">Daily (proven)</option><option value="4h">4-hour</option><option value="1h">1-hour</option></select></label>' +
        '<label class="btc-f"><span>Donchian period</span><input id="btc-per" type="number" value="20"></label>' +
        '<label class="btc-chk"><input type="checkbox" id="btc-short"> Trade both long &amp; short (on by default)</label>' +
        '<label class="btc-f"><span>Phone alerts — ntfy topic</span><input id="btc-ntfy" placeholder="your secret topic"></label>' +
        '<button id="btc-save" class="btc-save">Save</button>' +
      '</div>' +
      '<div id="btc-foot">Long+short daily trend (8y backtest: profits in up AND down trends at low cost; long-only scored higher but misses downtrends). Patient — trades last days-weeks, honour the stop. NOT advice.</div>' +
      '</div>';
    document.body.appendChild(p);
    $("btc-min").onclick = () => p.classList.toggle("min");
    $("btc-gear").onclick = () => $("btc-set").classList.toggle("hidden");
    p.querySelectorAll(".btc-tab").forEach((t) => t.onclick = () => {
      CHART.sym = t.dataset.s;
      p.querySelectorAll(".btc-tab").forEach((x) => x.classList.toggle("active", x === t));
      drawChart();
    });
    $("btc-int").value = CFG.interval; $("btc-per").value = CFG.period; $("btc-short").checked = CFG.allowShort; $("btc-ntfy").value = CFG.ntfyTopic;
    $("btc-save").onclick = () => {
      CFG.interval = $("btc-int").value; CFG.period = parseInt($("btc-per").value, 10) || 20;
      CFG.allowShort = $("btc-short").checked; CFG.ntfyTopic = $("btc-ntfy").value.trim();
      saveCfg(); $("btc-set").classList.add("hidden"); refresh();
    };
    // drag
    const h = $("btc-head"); let sx, sy, ox, oy, drag = false;
    h.addEventListener("mousedown", (e) => { if (e.target.classList.contains("ic")) return; drag = true; sx = e.clientX; sy = e.clientY; const r = p.getBoundingClientRect(); ox = r.left; oy = r.top; e.preventDefault(); });
    document.addEventListener("mousemove", (e) => { if (!drag) return; p.style.left = (ox + e.clientX - sx) + "px"; p.style.top = (oy + e.clientY - sy) + "px"; p.style.right = "auto"; });
    document.addEventListener("mouseup", () => { drag = false; });
  }
  function ensurePanel() { if (!$("btc-panel")) build(); }

  // Mini chart — the extension can't draw on TradingView's own chart (locked canvas),
  // so it draws the candles + Donchian levels here. For the full on-chart view use the
  // btc_donchian.pine script, which runs inside TradingView and marks the chart directly.
  function ensureChart() {
    if (CHART.obj || !window.LightweightCharts) return;
    const div = $("btc-chart"); if (!div || !div.clientWidth) return;
    CHART.obj = LightweightCharts.createChart(div, {
      height: 150, width: div.clientWidth,
      layout: { background: { color: "transparent" }, textColor: "#8b95a3", fontSize: 10 },
      grid: { vertLines: { color: "#141b24" }, horzLines: { color: "#141b24" } },
      timeScale: { timeVisible: CFG.interval !== "1d", secondsVisible: false, borderColor: "#1b232e" },
      rightPriceScale: { borderColor: "#1b232e" }, handleScroll: false, handleScale: false
    });
    CHART.series = CHART.obj.addCandlestickSeries({ upColor: "#3fcf6b", downColor: "#ff5b6b", borderVisible: false, wickUpColor: "#3fcf6b", wickDownColor: "#ff5b6b" });
  }
  function drawChart() {
    ensureChart();
    if (!CHART.series || !lastStates) return;
    const st = lastStates[SYMS.indexOf(CHART.sym)];
    if (!st || st.err || !st.bars || !st.bars.length) return;
    CHART.series.setData(st.bars.map((b) => ({ time: b.t, open: b.o, high: b.h, low: b.l, close: b.c })));
    CHART.lines.forEach((l) => CHART.series.removePriceLine(l)); CHART.lines = [];
    const add = (price, color, title) => { if (price) CHART.lines.push(CHART.series.createPriceLine({ price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title })); };
    if (st.pos) {
      add(st.entry, "#4cc2ff", "Entry");
      add(st.stop, "#ff5b6b", st.entryBarsAgo <= 5 ? "SL" : "Trail");
      if (st.entryBarsAgo <= 5) { add(st.t1, "#e0b341", "T1"); add(st.t2, "#3fcf6b", "T2"); }
    } else {
      add(st.upBreak, "#3fcf6b", "Long>"); if (CFG.allowShort) add(st.dnBreak, "#ff5b6b", "Short<");
    }
    CHART.obj.timeScale().fitContent();
  }

  // Performance tracking — reads the trades the background worker journaled.
  function renderTrades() {
    try {
      chrome.storage.local.get(["btcjournal"], (r) => {
        const jr = r.btcjournal || [], sEl = $("btc-stats"), jEl = $("btc-journal");
        if (sEl) {
          if (!jr.length) sEl.innerHTML = '<div class="btc-det">📓 No closed trades tracked yet — fills as trends complete.</div>';
          else {
            const n = jr.length, wins = jr.filter((x) => x.R > 0).length, tot = jr.reduce((a, b) => a + b.R, 0), exp = tot / n;
            sEl.innerHTML = `<div class="btc-strow"><span>Trades<b>${n}</b></span><span>Win<b>${Math.round(wins / n * 100)}%</b></span><span>Total<b class="${tot >= 0 ? "pos" : "neg"}">${tot.toFixed(1)}R</b></span><span>Exp<b class="${exp >= 0 ? "pos" : "neg"}">${exp.toFixed(2)}R</b></span></div>`;
          }
        }
        if (jEl) jEl.innerHTML = jr.slice(-5).reverse().map((x) => `<div class="btc-je"><span>${x.side} ${x.sym} @${fmt(x.entry)}→${fmt(x.exit)}</span><b class="${x.R > 0 ? "pos" : "neg"}">${x.R > 0 ? "+" : ""}${x.R}R</b></div>`).join("");
      });
    } catch (e) {}
  }
  try { chrome.storage.onChanged.addListener((ch, area) => { if (area === "local" && ch.btcjournal) renderTrades(); }); } catch (e) {}

  function fmt(n) { return n >= 1000 ? n.toLocaleString("en-US", { maximumFractionDigits: 0 }) : n.toFixed(2); }
  function rowHtml(st, label) {
    const pos = st.pos === 1 ? "LONG" : st.pos === -1 ? "SHORT" : "FLAT";
    const cls = st.pos === 1 ? "long" : st.pos === -1 ? "short" : "flat";
    const pnl = st.pos ? `<b class="${st.pnl >= 0 ? "pos" : "neg"}">${st.pnl >= 0 ? "+" : ""}${st.pnl.toFixed(1)}%</b>` : "";
    let detail;
    if (!st.pos) detail = `waiting — LONG if close &gt; $${fmt(st.upBreak)}${CFG.allowShort ? ` · SHORT if &lt; $${fmt(st.dnBreak)}` : ""}`;
    else if (st.entryBarsAgo <= 5) detail = `fresh ${pos} @ $${fmt(st.entry)} · stop $${fmt(st.stop)} · T1 $${fmt(st.t1)} · T2 $${fmt(st.t2)}`;
    else detail = `${pos} @ $${fmt(st.entry)} (${st.entryBarsAgo} bars ago) · trailing stop $${fmt(st.stop)} — exit if price crosses it`;
    return `<div class="btc-row"><div class="btc-r1"><span class="btc-sym">${label}</span><span class="btc-badge ${cls}">${pos}</span><span class="btc-px">$${fmt(st.px)}</span>${pnl}</div><div class="btc-det">${detail}</div></div>`;
  }

  function refresh() {
    ensurePanel();
    const mode = $("btc-mode"); if (mode) mode.textContent = CFG.interval + (CFG.allowShort ? " L/S" : " L");
    try {
      chrome.runtime.sendMessage({ type: "btcstates", cfg: CFG }, (states) => {
        const el = $("btc-rows"); if (!el) return;
        if (!states || !states.length) { el.innerHTML = '<div class="btc-det">No crypto source reachable — retrying…</div>'; return; }
        el.innerHTML = states.map((st, i) => st && !st.err ? rowHtml(st, SYMS[i]) : `<div class="btc-det">${SYMS[i]}: ${(st && st.err) || "no data"}</div>`).join("");
        lastStates = states; drawChart();
        const src = states.find((s) => s && s.source);
        if (mode && src) mode.textContent = CFG.interval + (CFG.allowShort ? " L/S" : " L") + " · " + src.source;
      });
    } catch (e) {}
  }

  loadCfg(() => { build(); refresh(); renderTrades(); setInterval(() => { refresh(); renderTrades(); }, 30000); setInterval(ensurePanel, 4000); });
})();
