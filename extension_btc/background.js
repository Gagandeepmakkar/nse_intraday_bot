// Background worker: checks the Donchian trend hourly and pushes a phone alert (ntfy) when
// BTC/ETH enters or exits a trend — so you don't have to watch. Low-frequency by design.
"use strict";
importScripts("engine.js");

const SYMS = ["BTC", "ETH"];

chrome.runtime.onInstalled.addListener(() => chrome.alarms.create("btc-check", { periodInMinutes: 15 }));
chrome.runtime.onStartup.addListener(() => chrome.alarms.create("btc-check", { periodInMinutes: 15 }));
chrome.alarms.onAlarm.addListener((a) => { if (a.name === "btc-check") check(); });

// The TradingView content script asks the background for state (Yahoo has no CORS header,
// so it must be fetched here where host permissions bypass CORS).
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.type !== "btcstates") return;
  (async () => {
    const out = [];
    for (const k of SYMS) {
      try { out.push(await self.BTCEngine.state(k, msg.cfg || {})); }
      catch (e) { out.push({ sym: k, err: String(e && e.message || e) }); }
    }
    sendResponse(out);
  })();
  return true;  // async response
});

async function pushNtfy(topic, title, text) {
  if (!topic) return;
  try { await fetch("https://ntfy.sh/" + topic, { method: "POST", headers: { "Title": title, "Priority": "high", "Tags": "chart_with_upwards_trend" }, body: text }); } catch (e) {}
}

async function check() {
  const cfg = Object.assign({ interval: "1d", period: 20, allowShort: true, ntfyTopic: "" },
    (await chrome.storage.local.get("btccfg")).btccfg || {});
  for (const sym of SYMS) {
    try {
      const st = await self.BTCEngine.state(sym, cfg);
      const key = "btctrade_" + sym;
      const prev = (await chrome.storage.local.get(key))[key];   // {pos,entry,risk0} | null | undefined
      const prevPos = prev ? prev.pos : 0;
      if (prev && prev.pos && prev.pos !== st.pos) {
        // the previous trade just closed at the current price — journal its result in R
        const R = prev.risk0 ? prev.pos * (st.px - prev.entry) / prev.risk0 : 0;
        const jr = (await chrome.storage.local.get("btcjournal")).btcjournal || [];
        jr.push({ t: Date.now(), sym, side: prev.pos === 1 ? "LONG" : "SHORT", entry: +prev.entry.toFixed(2), exit: +st.px.toFixed(2), R: +R.toFixed(2) });
        await chrome.storage.local.set({ btcjournal: jr.slice(-200) });
      }
      if (prev !== undefined && prevPos !== st.pos) {   // trend changed -> alert
        let msg;
        if (st.pos === 1) msg = `🟢 ${sym}: Donchian breakout — ENTER LONG near $${Math.round(st.px)}, stop $${Math.round(st.stop)}`;
        else if (st.pos === -1) msg = `🔴 ${sym}: broke down — ENTER SHORT near $${Math.round(st.px)}, stop $${Math.round(st.stop)}`;
        else msg = `⚪ ${sym}: trend over — EXIT at $${Math.round(st.px)}`;
        await pushNtfy(cfg.ntfyTopic, sym + " trend change", msg);
      }
      await chrome.storage.local.set({ [key]: st.pos ? { pos: st.pos, entry: st.entry, risk0: st.risk0 } : null });
    } catch (e) {}
  }
}
