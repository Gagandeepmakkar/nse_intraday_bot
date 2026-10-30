"""Tiny localhost publisher — serve the bot's latest picks to the browser extension.

The extension (extension/) fetches http://127.0.0.1:8787/picks to auto-load the two
stocks the bot selected, then runs its own live algorithms on their charts. Reads the
most recent run from signals.db; no new dependencies (stdlib http.server).

Run: python serve_picks.py      (or ./run.sh serve)
"""
import json
import sqlite3
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import analyze as analyzer

DB = "signals.db"
PORT = 8787


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

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/picks"):
            self._send(200, json.dumps(latest_picks()).encode())
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
