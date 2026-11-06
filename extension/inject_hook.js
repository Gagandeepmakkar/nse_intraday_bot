// Runs in Groww's PAGE context (world: MAIN). Captures Groww's own candle-API
// request — its URL (symbol + interval) and the Authorization token — by wrapping
// fetch and XHR, then hands them to the overlay via postMessage. No token is stored;
// we just reuse whatever Groww itself is already sending, so it auto-refreshes.
(function () {
  "use strict";
  function relay(url, auth) {
    if (url && url.indexOf("charting_service") !== -1) {
      window.postMessage({ __nlcGroww: true, url: String(url), auth: auth || "" }, "*");
    }
  }

  const origFetch = window.fetch;
  window.fetch = function (input, init) {
    try {
      const url = typeof input === "string" ? input : (input && input.url);
      let auth = "";
      if (init && init.headers) {
        auth = init.headers instanceof Headers
          ? (init.headers.get("Authorization") || "")
          : (init.headers.Authorization || init.headers.authorization || "");
      }
      if (!auth && input && input.headers && input.headers.get) auth = input.headers.get("Authorization") || "";
      relay(url, auth);
    } catch (e) {}
    return origFetch.apply(this, arguments);
  };

  const origOpen = XMLHttpRequest.prototype.open;
  const origSetH = XMLHttpRequest.prototype.setRequestHeader;
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, url) { this.__nlcUrl = url; return origOpen.apply(this, arguments); };
  XMLHttpRequest.prototype.setRequestHeader = function (k, v) {
    if (String(k).toLowerCase() === "authorization") this.__nlcAuth = v;
    return origSetH.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function () { try { relay(this.__nlcUrl, this.__nlcAuth); } catch (e) {} return origSend.apply(this, arguments); };
})();
