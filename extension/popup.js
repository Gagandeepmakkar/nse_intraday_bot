/* Popup orchestrator: load the bot's 2 picks, and for the selected one fetch live
 * 1-min candles, run the multi-algorithm engine, and render a live chart + a fresh
 * BUY/SELL/HOLD verdict with levels computed NOW (not the bot's earlier prediction).
 * Auto-refreshes every 30s while open. */
(function () {
  "use strict";
  const IST = 19800; // seconds; feed as UTC so lightweight-charts' axis reads IST
  let picks = [];
  let active = 0;
  let chart = null, series = null, priceLines = [];
  let timer = null;

  const $ = id => document.getElementById(id);

  async function loadPicks() {
    const fromBot = await Data.fetchPicks();
    if (fromBot && fromBot.length) {
      picks = fromBot;
      chrome.storage && chrome.storage.local.set({ picks });
      return;
    }
    const saved = await new Promise(res =>
      chrome.storage ? chrome.storage.local.get("picks", d => res(d.picks)) : res(null));
    if (saved && saved.length) { picks = saved; return; }
    picks = [];
  }

  function renderTabs() {
    const el = $("tabs"); el.innerHTML = "";
    picks.forEach((p, i) => {
      const t = document.createElement("div");
      t.className = "tab" + (i === active ? " active" : "");
      t.innerHTML = `${p.symbol}<span class="sub">${p.side || ""} ${p.score ? "· " + Number(p.score).toFixed(0) : ""}</span>`;
      t.onclick = () => { active = i; renderTabs(); refresh(); };
      el.appendChild(t);
    });
  }

  function ensureChart() {
    if (chart) return;
    chart = LightweightCharts.createChart($("chart"), {
      height: 200, width: $("chart").clientWidth,
      layout: { background: { color: "transparent" }, textColor: "#9aa4b2" },
      grid: { vertLines: { color: "#1b232e" }, horzLines: { color: "#1b232e" } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#273040" },
      rightPriceScale: { borderColor: "#273040" }
    });
    series = chart.addCandlestickSeries({
      upColor: "#2ecc71", downColor: "#e74c3c", borderVisible: false,
      wickUpColor: "#2ecc71", wickDownColor: "#e74c3c"
    });
  }

  function drawLevels(levels) {
    priceLines.forEach(l => series.removePriceLine(l));
    priceLines = [];
    if (!levels) return;
    const add = (price, color, title) => {
      if (price) priceLines.push(series.createPriceLine(
        { price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title }));
    };
    add(levels.entry, "#38bdf8", "Entry");
    add(levels.stop, "#f87171", "Stop");
    add(levels.target1, "#fbbf24", "T1");
    add(levels.target2, "#4ade80", "T2");
  }

  function renderVerdict(d, side) {
    $("action").className = "action " + d.action;
    $("action").textContent = d.action;
    $("confidence").textContent = d.confidence ? `${d.confidence}% agree` : "";
    $("side").textContent = side ? `bot side: ${side}` : "";
    const L = d.levels;
    $("levels").innerHTML = L ? [
      ["Entry", L.entry], ["Stop", L.stop], ["T1", L.target1], ["T2", L.target2]
    ].map(([k, v]) => `<div class="lv"><div class="k">${k}</div><div class="v">₹${v.toFixed(2)}</div></div>`).join("")
      : `<div class="lv" style="grid-column:1/-1"><div class="k">No trade</div><div class="v">algos not aligned${side ? " with " + side : ""}</div></div>`;
    $("votes").innerHTML = d.votes.map(v =>
      `<span class="vote ${v.signal}" title="${v.note}">${v.name}: ${v.signal}</span>`).join("");
  }

  async function refresh() {
    if (!picks.length) { $("manual").classList.remove("hidden"); $("status").textContent = "no picks"; return; }
    $("manual").classList.add("hidden");
    const p = picks[active];
    $("status").textContent = "updating " + p.symbol + "…";
    try {
      const candles = await Data.fetchCandles(p.symbol);
      if (!candles.length) { $("status").textContent = "no live data for " + p.symbol; return; }
      ensureChart();
      series.setData(candles.map(c => ({
        time: c.t + IST, open: c.o, high: c.h, low: c.l, close: c.c
      })));
      const d = Algos.decide(candles, (p.side || "").toUpperCase());
      drawLevels(d.levels);
      chart.timeScale().fitContent();
      renderVerdict(d, p.side);
      const now = new Date();
      $("status").textContent = `updated ${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
    } catch (e) {
      $("status").textContent = "error: " + e.message;
    }
  }

  function startManual() {
    $("manualGo").onclick = () => {
      const syms = $("manualInput").value.split(",").map(s => s.trim().toUpperCase()).filter(Boolean).slice(0, 2);
      if (!syms.length) return;
      picks = syms.map(s => ({ symbol: s, side: "" }));
      chrome.storage && chrome.storage.local.set({ picks });
      active = 0; renderTabs(); refresh();
    };
  }

  async function init() {
    startManual();
    await loadPicks();
    renderTabs();
    await refresh();
    timer = setInterval(refresh, 30000);
    window.addEventListener("resize", () => chart && chart.applyOptions({ width: $("chart").clientWidth }));
  }

  document.addEventListener("DOMContentLoaded", init);
})();
