/* Groww overlay: "Analyze this stock" button → reads the stock you're viewing off
 * the page, then runs the DEEP PYTHON backend (multi-timeframe indicators + a live
 * mini-backtest of the setup on THIS stock) and shows a verdict WITH evidence +
 * exact levels on a live chart. Takes a few seconds because it's doing real work.
 * Confirmation only — trade manually. Needs the local backend: `./run.sh serve`.
 *
 * Symbol detection (no pixel scraping): Groww's embedded Next.js JSON, then a visible
 * "NSE: TICKER", then a bot pick seen on the page. Else type it. */
(function () {
  "use strict";
  if (window.__nlcLoaded) return;
  window.__nlcLoaded = true;

  const IST = 19800;
  const state = { symbol: "", picks: [], chart: null, series: null, lines: [], timer: null };

  function ask(type, extra) {
    return new Promise(res => {
      try { chrome.runtime.sendMessage(Object.assign({ type }, extra), r => res(r || { ok: false })); }
      catch (e) { res({ ok: false, error: String(e) }); }
    });
  }
  const el = (tag, attrs, html) => {
    const e = document.createElement(tag);
    if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (html != null) e.innerHTML = html;
    return e;
  };
  const $ = id => document.getElementById(id);

  function detectSymbol() {
    const nd = $("__NEXT_DATA__");
    if (nd && nd.textContent) {
      const m = nd.textContent.match(/"(?:nseScriptCode|nse_scrip_code|nseSymbol|growwContractId)"\s*:\s*"([A-Z0-9&]{2,20})"/);
      if (m) return m[1];
    }
    const body = document.body.innerText || "";
    const m2 = body.match(/\bNSE\b[\s:·|\-]+([A-Z][A-Z0-9&]{1,19})\b/);
    if (m2) return m2[1];
    for (const p of state.picks) {
      const s = (p.symbol || "").toUpperCase();
      if (s && new RegExp("\\b" + s + "\\b").test(body.toUpperCase())) return s;
    }
    return "";
  }

  function build() {
    const p = el("div", { id: "nse-live-confirm" });
    p.innerHTML =
      '<div id="nlc-head"><span class="t">Live Confirm</span><span class="s" id="nlc-status">ready</span>' +
      '<span id="nlc-min" title="minimize">—</span></div>' +
      '<div id="nlc-body">' +
      '<button id="nlc-analyze" style="width:100%;padding:9px;margin-bottom:9px;background:#38bdf8;border:0;border-radius:8px;color:#06222e;font-weight:800;font-size:13px;cursor:pointer">📈 Analyze this stock</button>' +
      '<div class="nlc-row"><input id="nlc-sym" placeholder="or type a symbol"><button id="nlc-go">Set</button></div>' +
      '<div id="nlc-verdict"><span id="nlc-action" class="HOLD">—</span>' +
      '<span id="nlc-conf"></span><span id="nlc-botside"></span></div>' +
      '<div id="nlc-levels"></div><div id="nlc-chart"></div><div id="nlc-evidence"></div>' +
      '<div id="nlc-foot">Deep backend analysis (multi-timeframe + recent-setup backtest). NOT advice. Execute manually.</div>' +
      '</div>';
    document.body.appendChild(p);
    $("nlc-min").onclick = () => p.classList.toggle("min");
    $("nlc-analyze").onclick = analyzeThisPage;
    $("nlc-go").onclick = () => { const v = $("nlc-sym").value.trim().toUpperCase(); if (v) run(v); };
    dragify(p, $("nlc-head"));
  }

  function dragify(box, handle) {
    let sx, sy, ox, oy, drag = false;
    handle.addEventListener("mousedown", e => {
      if (e.target.id === "nlc-min") return;
      drag = true; sx = e.clientX; sy = e.clientY;
      const r = box.getBoundingClientRect(); ox = r.left; oy = r.top; e.preventDefault();
    });
    document.addEventListener("mousemove", e => {
      if (!drag) return;
      box.style.left = (ox + e.clientX - sx) + "px"; box.style.top = (oy + e.clientY - sy) + "px"; box.style.right = "auto";
    });
    document.addEventListener("mouseup", () => { drag = false; });
  }

  function ensureChart() {
    if (state.chart) return;
    const div = $("nlc-chart");
    state.chart = LightweightCharts.createChart(div, {
      height: 170, width: div.clientWidth,
      layout: { background: { color: "transparent" }, textColor: "#9aa4b2" },
      grid: { vertLines: { color: "#1b232e" }, horzLines: { color: "#1b232e" } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#273040" },
      rightPriceScale: { borderColor: "#273040" }
    });
    state.series = state.chart.addCandlestickSeries({
      upColor: "#2ecc71", downColor: "#e74c3c", borderVisible: false,
      wickUpColor: "#2ecc71", wickDownColor: "#e74c3c"
    });
  }

  function drawLevels(L) {
    state.lines.forEach(l => state.series.removePriceLine(l));
    state.lines = [];
    if (!L) return;
    const add = (price, color, title) => { if (price) state.lines.push(
      state.series.createPriceLine({ price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title })); };
    add(L.entry, "#38bdf8", "Entry"); add(L.stop, "#f87171", "SL");
    add(L.target1, "#fbbf24", "T1"); add(L.target2, "#4ade80", "T2");
  }

  const botSide = symbol => {
    const m = state.picks.find(p => (p.symbol || "").toUpperCase() === symbol);
    return m ? (m.side || "") : "";
  };

  function analyzeThisPage() {
    const sym = detectSymbol();
    if (!sym) { $("nlc-status").textContent = "couldn't read symbol — type it below"; return; }
    $("nlc-sym").value = sym;
    run(sym);
  }

  function run(symbol) {
    state.symbol = symbol;
    if (state.timer) clearInterval(state.timer);
    refresh();
    state.timer = setInterval(refresh, 60000);  // deep analysis is heavy; refresh each minute
  }

  async function refresh() {
    if (!state.symbol) return;
    const sym = state.symbol;
    $("nlc-status").textContent = sym + " · analyzing (deep, ~5-15s)…";
    // Chart first (fast), so you see the candles while the backend thinks.
    const cr = await ask("candles", { symbol: sym });
    if (cr.ok && cr.candles && cr.candles.length) {
      ensureChart();
      state.series.setData(cr.candles.map(c => ({ time: c.t + IST, open: c.o, high: c.h, low: c.l, close: c.c })));
      state.chart.timeScale().fitContent();
    }
    // Deep backend verdict.
    const ar = await ask("analyze", { symbol: sym, side: botSide(sym) });
    if (!ar.ok) { $("nlc-status").textContent = "backend offline — run `./run.sh serve` (" + (ar.error || "") + ")"; return; }
    renderDeep(ar.result);
  }

  function renderDeep(r) {
    const e = r.evidence || {};
    const bt = e.setup_backtest || {};
    const act = $("nlc-action");
    act.className = r.action || "WAIT"; act.textContent = r.action || "WAIT";
    $("nlc-conf").textContent = r.confidence ? r.confidence + "% confidence" : "";
    $("nlc-botside").textContent = r.side ? (botSide(r.symbol) ? "bot pick: " + r.side : "side: " + r.side) : "";
    if (state.series) drawLevels(r.levels);
    $("nlc-levels").innerHTML = r.levels ? [
      ["Entry", r.levels.entry], ["SL", r.levels.stop], ["T1", r.levels.target1], ["T2", r.levels.target2]
    ].map(([k, v]) => `<div class="nlc-lv"><div class="k">${k}</div><div class="v">₹${Number(v).toFixed(2)}</div></div>`).join("")
      : '<div class="nlc-lv" style="grid-column:1/-1"><div class="k">No trade</div><div class="v">' + (r.error ? r.error : "setup has no recent edge — hold") + '</div></div>';
    const warn = (bt.expectancy_rs_per_10qty != null && bt.expectancy_rs_per_10qty <= 0) ? " ⚠" : "";
    $("nlc-evidence").innerHTML =
      `<div class="nlc-ev"><b>5m</b> ${e.intraday_trend_5m || "?"} · <b>Daily</b> ${e.daily_trend || "?"} · <b>RSI</b> ${e.rsi != null ? e.rsi : "?"}</div>` +
      (bt.sessions ? `<div class="nlc-ev">Recent setup on this stock: <b>${bt.sessions}</b> sessions, <b>${bt.win_rate_pct}%</b> win, expectancy <b>₹${bt.expectancy_rs_per_10qty}</b>${warn}</div>` : "") +
      (e.checks_passed ? `<div class="nlc-ev">Confluence checks passed: <b>${e.checks_passed}</b></div>` : "");
    const now = new Date();
    $("nlc-status").textContent = r.symbol + " · " + String(now.getHours()).padStart(2, "0") + ":" + String(now.getMinutes()).padStart(2, "0");
  }

  async function init() {
    build();
    const pr = await ask("picks");
    state.picks = (pr && pr.ok && pr.picks) ? pr.picks : [];
    window.addEventListener("resize", () => state.chart && state.chart.applyOptions({ width: $("nlc-chart").clientWidth }));
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
