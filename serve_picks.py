"""Tiny localhost publisher — serve the bot's latest picks to the browser extension.

The extension (extension/) fetches http://127.0.0.1:8787/picks to auto-load the two
stocks the bot selected, then runs its own live algorithms on their charts. Reads the
most recent run from signals.db; no new dependencies (stdlib http.server).

Run: python serve_picks.py      (or ./run.sh serve)
"""
import json
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import analyze as analyzer

DB = "signals.db"
PORT = 8787
JOURNAL = "journal.jsonl"   # append-only record of every signal/outcome the extension emits
_NEWS_CACHE = {}            # { "SYMBOL|YYYY-MM-DD": {sentiment, caution, headlines} }


def news_verdict(symbol):
    """Per-symbol news read for the extension's confluence signal: Gemini (if a key is
    set) else keyword sentiment, plus an event-risk caution. Cached per symbol per day
    so we don't refetch on every signal. Deterministic-safe: never predicts price."""
    from datetime import date
    key = f"{symbol}|{date.today().isoformat()}"
    if key in _NEWS_CACHE:
        return _NEWS_CACHE[key]
    out = {"sentiment": "neutral", "caution": "none", "headlines": []}
    try:
        import briefing
        heads = briefing.fetch_news(symbol)
        out["headlines"] = heads[:5]
        ai = briefing.ai_news(symbol, heads)
        if ai:
            out["sentiment"], out["caution"] = ai[0], ai[1]
        else:
            out["sentiment"] = briefing.sentiment(heads)[0].split()[0]  # "bullish"/"bearish"/"mixed"
    except Exception as e:
        out["caution"] = f"(news unavailable: {e})"
    _NEWS_CACHE[key] = out
    return out


def append_log(events):
    """Append one or more event dicts as JSON lines, de-duplicated by (event,t,sym)."""
    if isinstance(events, dict):
        events = [events]
    seen = set()
    try:
        with open(JOURNAL, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                    seen.add((e.get("event"), e.get("t"), e.get("sym")))
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    wrote = 0
    with open(JOURNAL, "a", encoding="utf-8") as f:
        for e in events:
            if not isinstance(e, dict):
                continue
            k = (e.get("event"), e.get("t"), e.get("sym"))
            if k in seen:
                continue
            seen.add(k)
            e.setdefault("logged_at", datetime.now(timezone.utc).isoformat())
            f.write(json.dumps(e) + "\n")
            wrote += 1
    return wrote


def latest_picks():
    con = sqlite3.connect(DB)
    try:
        row = con.execute("SELECT created_at FROM signals_v2 ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            return []
        rows = con.execute(
            "SELECT symbol,side,entry,stop,target1,target2,label,score "
            "FROM signals_v2 WHERE created_at=?", (row[0],)).fetchall()
    finally:
        con.close()
    return [{"symbol": s, "side": side, "entry": entry, "stop": stop, "target1": t1,
             "target2": t2, "label": label, "score": score}
            for s, side, entry, stop, t1, t2, label, score in rows]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body=b""):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204)

    def do_POST(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/log"):
            self._send(404, b'{"error":"not found"}')
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
            events = payload.get("events", payload)
            wrote = append_log(events)
            self._send(200, json.dumps({"ok": True, "wrote": wrote}).encode())
        except Exception as e:
            self._send(400, json.dumps({"ok": False, "error": str(e)}).encode())

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/picks"):
            self._send(200, json.dumps(latest_picks()).encode())
        elif parsed.path.startswith("/news"):
            q = parse_qs(parsed.query)
            sym = (q.get("symbol") or [""])[0].strip().upper()
            if not sym:
                self._send(400, b'{"error":"symbol required"}')
                return
            self._send(200, json.dumps(news_verdict(sym)).encode())
        elif parsed.path.startswith("/analyze"):
            q = parse_qs(parsed.query)
            sym = (q.get("symbol") or [""])[0].strip().upper()
            side = (q.get("side") or [""])[0].strip()
            if not sym:
                self._send(400, b'{"error":"symbol required"}')
                return
            try:
                res = analyzer.analyze(sym, side)
            except Exception as e:
                res = {"symbol": sym, "action": "WAIT", "error": str(e), "evidence": {}}
            self._send(200, json.dumps(res).encode())
        else:
            self._send(404, b'{"error":"not found"}')

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"Serving on http://127.0.0.1:{PORT}  —  /picks and /analyze?symbol=SYM&side=LONG  (Ctrl-C to stop)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
