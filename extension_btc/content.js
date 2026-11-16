// BTC/ETH Donchian trend panel on TradingView. Reads free Binance data via engine.js,
// shows the current trend position (LONG/SHORT/FLAT) + exact levels for BTC & ETH, 24/7.
(function () {
  "use strict";
  if (window.__btcTrendLoaded) return;
  window.__btcTrendLoaded = true;
  const SYMS = ["BTC", "ETH"];   // data is fetched by the background worker (handles CORS/geo)
  const DEFAULTS = { interval: "1d", period: 20, allowShort: true, ntfyTopic: "" };
  let CFG = Object.assign({}, DEFAULTS);
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
        const src = states.find((s) => s && s.source);
        if (mode && src) mode.textContent = CFG.interval + (CFG.allowShort ? " L/S" : " L") + " · " + src.source;
      });
    } catch (e) {}
  }

  loadCfg(() => { build(); refresh(); setInterval(refresh, 30000); setInterval(ensurePanel, 4000); });
})();
