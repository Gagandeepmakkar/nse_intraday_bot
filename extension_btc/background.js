// Background worker: checks the Donchian trend hourly and pushes a phone alert (ntfy) when
// BTC/ETH enters or exits a trend — so you don't have to watch. Low-frequency by design.
"use strict";
importScripts("engine.js");

const SYMS = ["BTC", "ETH"];

chrome.runtime.onInstalled.addListener(() => chrome.alarms.create("btc-check", { periodInMinutes: 60 }));
chrome.runtime.onStartup.addListener(() => chrome.alarms.create("btc-check", { periodInMinutes: 60 }));
chrome.alarms.onAlarm.addListener((a) => { if (a.name === "btc-check") check(); });

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
      const key = "btcpos_" + sym;
      const prev = (await chrome.storage.local.get(key))[key];
      if (prev !== undefined && prev !== st.pos) {
        let msg;
        if (st.pos === 1) msg = `🟢 ${sym}: Donchian breakout — ENTER LONG near $${Math.round(st.px)}, stop $${Math.round(st.stop)}`;
        else if (st.pos === -1) msg = `🔴 ${sym}: broke down — ENTER SHORT near $${Math.round(st.px)}, stop $${Math.round(st.stop)}`;
        else msg = `⚪ ${sym}: trend over — EXIT (trailing stop hit)`;
        await pushNtfy(cfg.ntfyTopic, sym + " trend change", msg);
      }
      await chrome.storage.local.set({ [key]: st.pos });
    } catch (e) {}
  }
}
