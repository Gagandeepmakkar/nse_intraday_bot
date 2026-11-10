/* MV3 service worker: the ONLY place allowed to fetch cross-origin (content scripts
 * on groww.in cannot, due to the page's CORS). The Groww overlay messages here for
 * live candles and the bot's picks. */
"use strict";

async function fetchCandles(symbol) {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}.NS?interval=1m&range=1d`;
  const r = await fetch(url);
  if (!r.ok) throw new Error(`Yahoo ${r.status}`);
  const j = await r.json();
  const res = j.chart && j.chart.result && j.chart.result[0];
  if (!res || !res.timestamp) return [];
  const ts = res.timestamp, q = res.indicators.quote[0];
  const out = [];
  for (let i = 0; i < ts.length; i++) {
    if (q.close[i] == null) continue;
    out.push({ t: ts[i], o: q.open[i], h: q.high[i], l: q.low[i], c: q.close[i], v: q.volume[i] || 0 });
  }
  return out;
}

async function fetchPicks() {
  try {
    const r = await fetch("http://127.0.0.1:8787/picks", { cache: "no-store" });
    return r.ok ? await r.json() : null;
  } catch (e) {
    return null;
  }
}

async function analyzeStock(symbol, side) {
  const u = `http://127.0.0.1:8787/analyze?symbol=${encodeURIComponent(symbol)}&side=${encodeURIComponent(side || "")}`;
  const r = await fetch(u, { cache: "no-store" });
  if (!r.ok) throw new Error(`backend ${r.status}`);
  return await r.json();
}

// Optional free Telegram push (content scripts can't call api.telegram.org due to CORS).
async function sendTelegram(token, chat, text) {
  const u = `https://api.telegram.org/bot${token}/sendMessage`;
  const r = await fetch(u, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ chat_id: chat, text }) });
  return await r.json();   // { ok, description, ... } — the REAL Telegram result
}
// Find the chat id of whoever last messaged the bot (after they press Start).
async function telegramChatId(token) {
  const r = await fetch(`https://api.telegram.org/bot${token}/getUpdates`);
  const j = await r.json();
  const ups = (j && j.result) || [];
  for (let i = ups.length - 1; i >= 0; i--) {
    const m = ups[i].message || ups[i].edited_message || (ups[i].my_chat_member);
    const id = m && m.chat && m.chat.id;
    if (id) return String(id);
  }
  return "";
}

// Free phone push via ntfy.sh — no account, just a topic name.
async function sendNtfy(topic, title, text) {
  const r = await fetch("https://ntfy.sh/" + encodeURIComponent(topic), {
    method: "POST",
    headers: { "Title": title || "VIM", "Priority": "high", "Tags": "chart_with_upwards_trend" },
    body: text || ""
  });
  return r.ok;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      if (msg.__log) {
        try {
          const r = await fetch("http://127.0.0.1:8787/log", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ events: msg.events }) });
          sendResponse({ ok: r.ok });
        } catch (e) { sendResponse({ ok: false }); }   // server not running — kept in chrome.storage, synced later
        return;
      }
      if (msg.__ntfy) { const ok = await sendNtfy(msg.topic, msg.title, msg.text); sendResponse({ ok }); return; }
      if (msg.__tgConnect) { const chat = await telegramChatId(msg.token); sendResponse({ ok: !!chat, chat }); return; }
      if (msg.__tg) { const res = await sendTelegram(msg.token, msg.chat, msg.text); sendResponse({ ok: !!(res && res.ok), desc: res && res.description }); return; }
      if (msg.type === "candles") sendResponse({ ok: true, candles: await fetchCandles(msg.symbol) });
      else if (msg.type === "picks") sendResponse({ ok: true, picks: await fetchPicks() });
      else if (msg.type === "analyze") sendResponse({ ok: true, result: await analyzeStock(msg.symbol, msg.side) });
      else sendResponse({ ok: false, error: "unknown message" });
    } catch (e) {
      sendResponse({ ok: false, error: String(e && e.message || e) });
    }
  })();
  return true; // keep the channel open for the async reply
});
