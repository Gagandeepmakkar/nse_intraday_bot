"""Source-agnostic market data.

KiteSource wraps Zerodha Kite Connect. DhanSource wraps DhanHQ (free historical
data; live quotes/depth may need Dhan's Data API plan). YahooSource wraps Yahoo
Finance via yfinance — fully free and token-free, but with no level-2 depth or
buy/sell quantities and ~15-min delayed quotes. MockSource generates
deterministic synthetic candles so the full pipeline can be verified offline.
All expose the same methods:

    instruments(exchange)            -> list[dict]  (rows: tradingsymbol,
                                                     instrument_token, segment,
                                                     instrument_type, exchange)
    quote(keys)                      -> dict keyed by "EXCH:SYMBOL" with
                                        last_price, volume, ohlc{open,high,low,close},
                                        buy_quantity, sell_quantity, depth{buy,sell}
    historical(token, start, end, i) -> list[dict]  (date, open, high, low, close, volume)

Shapes match Kite's so the screening code is identical across sources.
"""
import io
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

TZ = ZoneInfo(os.getenv("MARKET_TIMEZONE", "Asia/Kolkata"))


class KiteSource:
    def __init__(self):
        from kiteconnect import KiteConnect
        api_key = os.getenv("KITE_API_KEY", "").strip()
        token_file = Path("token.json")
        if not api_key or not token_file.exists():
            raise RuntimeError("Missing KITE_API_KEY or token.json. Run python auth.py first.")
        token = json.loads(token_file.read_text(encoding="utf-8"))["access_token"]
        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(token)
        self.kite.profile()  # Validates token and permissions.

    def instruments(self, exchange="NSE"):
        return self.kite.instruments(exchange)

    def quote(self, keys):
        # Kite allows up to 500 instruments per quote call; chunk conservatively.
        out = {}
        for i in range(0, len(keys), 400):
            out.update(self.kite.quote(keys[i:i + 400]))
        return out

    def historical(self, token, start, end, interval):
        return self.kite.historical_data(token, start, end, interval)


# --------------------------------------------------------------------------- #
# DhanHQ support
# --------------------------------------------------------------------------- #
# Dhan's scrip master (symbol -> numeric security_id). Compact CSV; column names
# are the SEM_* headers published by Dhan. If Dhan changes the CSV layout, adjust
# these three constants — nothing else depends on the header names.
DHAN_SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
DHAN_COL_EXCH = "SEM_EXM_EXCH_ID"       # "NSE" / "BSE"
DHAN_COL_SYMBOL = "SEM_TRADING_SYMBOL"  # e.g. "RELIANCE"
DHAN_COL_SECID = "SEM_SMST_SECURITY_ID"  # numeric security id
DHAN_COL_SERIES = "SEM_SERIES"          # "EQ" for cash equity


def _dhan_candles_to_rows(data, tz=TZ):
    """Map a DhanHQ intraday-candle payload to Kite-shaped candle rows.

    DhanHQ returns column-oriented arrays: {open, high, low, close, volume,
    timestamp}. timestamp is epoch seconds; converted to tz-naive IST. If candle
    clock-times later look shifted by 5h30m, flip the utc/convert handling here.
    """
    if not data or "timestamp" not in data:
        return []
    ts = pd.to_datetime(data["timestamp"], unit="s", utc=True).tz_convert(tz).tz_localize(None)
    df = pd.DataFrame({
        "date": ts, "open": data.get("open"), "high": data.get("high"),
        "low": data.get("low"), "close": data.get("close"), "volume": data.get("volume"),
    })
    return df.to_dict("records")


def _dhan_quote_to_common(payload, secid_to_sym, segment="NSE_EQ"):
    """Map a DhanHQ market-quote payload to our common per-symbol quote dict.

    Expected shape: {"data": {"NSE_EQ": {"<security_id>": {last_price, volume,
    ohlc:{open,high,low,close}, buy_quantity, sell_quantity, depth:{buy,sell}}}}}.
    All lookups are defensive so a renamed field degrades to a missing component
    rather than crashing the scan.
    """
    out = {}
    seg = ((payload or {}).get("data") or {}).get(segment, {})
    for secid, q in seg.items():
        sym = secid_to_sym.get(str(secid))
        if not sym or not q:
            continue
        ohlc = q.get("ohlc") or {}
        out[f"NSE:{sym}"] = {
            "last_price": q.get("last_price"),
            "volume": q.get("volume"),
            "average_price": q.get("average_price"),
            "ohlc": {"open": ohlc.get("open"), "high": ohlc.get("high"),
                     "low": ohlc.get("low"), "close": ohlc.get("close")},
            "buy_quantity": q.get("buy_quantity") or q.get("total_buy_quantity") or 0,
            "sell_quantity": q.get("sell_quantity") or q.get("total_sell_quantity") or 0,
            "depth": q.get("depth") or {},
        }
    return out


class DhanSource:
    """DhanHQ v2 adapter. Historical candles are free; live quote/depth may need
    Dhan's Data API subscription. Credentials come from DHAN_CLIENT_ID and
    DHAN_ACCESS_TOKEN (generate the access token in the Dhan web dashboard).

    NOTE: not verified against the live API in this build — field names/constants
    follow DhanHQ v2 docs and are marked for adjustment if the API differs.
    """
    _SEGMENT = "NSE_EQ"

    def __init__(self):
        client_id = os.getenv("DHAN_CLIENT_ID", "").strip()
        access_token = os.getenv("DHAN_ACCESS_TOKEN", "").strip()
        if not client_id or not access_token:
            raise RuntimeError("Missing DHAN_CLIENT_ID or DHAN_ACCESS_TOKEN in .env.")
        from dhanhq import dhanhq
        self.dhan = dhanhq(client_id, access_token)
        self._rows, self._secid_to_sym, self._sym_to_secid = self._load_scrip_master()

    def _load_scrip_master(self):
        resp = requests.get(DHAN_SCRIP_MASTER_URL, timeout=30)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text), low_memory=False)
        missing = [c for c in (DHAN_COL_EXCH, DHAN_COL_SYMBOL, DHAN_COL_SECID) if c not in df.columns]
        if missing:
            raise RuntimeError(f"Dhan scrip master missing columns {missing}; "
                               f"got {list(df.columns)[:10]}... Adjust DHAN_COL_* constants.")
        nse = df[df[DHAN_COL_EXCH] == "NSE"]
        if DHAN_COL_SERIES in nse.columns:
            nse = nse[nse[DHAN_COL_SERIES].astype(str).str.upper() == "EQ"]
        rows, secid_to_sym, sym_to_secid = [], {}, {}
        for _, r in nse.iterrows():
            sym = str(r[DHAN_COL_SYMBOL]).strip().upper()
            secid = str(r[DHAN_COL_SECID]).strip()
            if not sym or not secid or secid.lower() == "nan":
                continue
            rows.append({"tradingsymbol": sym, "instrument_token": secid,
                         "segment": "NSE", "instrument_type": "EQ", "exchange": "NSE"})
            secid_to_sym[secid] = sym
            sym_to_secid[sym] = secid
        return rows, secid_to_sym, sym_to_secid

    def instruments(self, exchange="NSE"):
        return self._rows

    def quote(self, keys):
        secids = []
        for k in keys:
            secid = self._sym_to_secid.get(k.split(":", 1)[-1])
            if secid:
                secids.append(secid)
        out = {}
        # DhanHQ market-quote allows up to 1000 instruments per request per segment.
        for i in range(0, len(secids), 1000):
            payload = self.dhan.quote_data({self._SEGMENT: secids[i:i + 1000]})
            out.update(_dhan_quote_to_common(payload, self._secid_to_sym, self._SEGMENT))
        return out

    def historical(self, token, start, end, interval):
        minutes = "".join(ch for ch in str(interval) if ch.isdigit()) or "5"
        resp = self.dhan.intraday_minute_data(
            security_id=str(token), exchange_segment=self._SEGMENT,
            instrument_type="EQUITY",
            from_date=start.strftime("%Y-%m-%d"), to_date=end.strftime("%Y-%m-%d"),
            interval=int(minutes))
        return _dhan_candles_to_rows(resp.get("data") if isinstance(resp, dict) else resp)


# --------------------------------------------------------------------------- #
# Yahoo Finance support (free, token-free)
# --------------------------------------------------------------------------- #
# yfinance exposes intraday OHLCV candles and index levels for NSE symbols at no
# cost. It does NOT expose level-2 depth or buy/sell quantities, so the order-flow
# component and the depth-based spread penalty degrade to neutral in the screen.
# Quotes are ~15 min delayed. NSE equities are suffixed ".NS"; indices use their
# Yahoo symbols.
YAHOO_INDEX = {"NIFTY 50": "^NSEI", "NIFTY BANK": "^NSEBANK"}


class YahooSource:
    """Free NSE data via yfinance. The universe is the curated symbols.txt list
    (there is no free full-universe quote feed). Depth/order-flow are unavailable
    and degrade to neutral in scoring; quotes are ~15 min delayed."""

    def __init__(self, lookback_days=5):
        import logging
        import yfinance as yf
        # The bot skips missing/renamed tickers gracefully; silence yfinance's own
        # error dumps so a stale symbol doesn't spam the output.
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        self._yf = yf
        self.lookback_days = max(lookback_days, 5)
        self.symbols = self._load_symbols()
        self._candles = None  # lazy cache {symbol: intraday DataFrame}
        self._daily = None    # lazy cache {symbol: daily DataFrame} for trend

    def _load_symbols(self):
        p = Path(os.getenv("SYMBOLS_FILE", "symbols.txt"))
        if not p.exists():
            raise RuntimeError("Missing symbols.txt — YahooSource needs it for the universe.")
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip().upper()
            if s and not s.startswith("#"):
                out.append(s)
        if not out:
            raise RuntimeError("symbols.txt has no usable symbols.")
        return out

    def instruments(self, exchange="NSE"):
        return [{"tradingsymbol": s, "instrument_token": s, "segment": "NSE",
                 "instrument_type": "EQ", "exchange": "NSE"} for s in self.symbols]

    @staticmethod
    def _quote_from_daily(sub):
        """Build a common quote dict from a daily OHLCV frame. ohlc.close is the
        PREVIOUS session's close (the screen uses it as prev_close)."""
        sub = sub.dropna(subset=["Close"])
        if len(sub) < 2:
            return None
        return {
            "last_price": float(sub["Close"].iloc[-1]),
            "volume": float(sub["Volume"].iloc[-1]),
            "average_price": None,
            "ohlc": {"open": float(sub["Open"].iloc[-1]), "high": float(sub["High"].iloc[-1]),
                     "low": float(sub["Low"].iloc[-1]), "close": float(sub["Close"].iloc[-2])},
            "buy_quantity": 0, "sell_quantity": 0, "depth": {},
        }

    def quote(self, keys):
        out, eq = {}, []
        for k in keys:
            name = k.split(":", 1)[-1]
            if name in YAHOO_INDEX:
                d = self._yf.Ticker(YAHOO_INDEX[name]).history(period="7d", interval="1d")
                q = self._quote_from_daily(d) if not d.empty else None
                if q:
                    out[k] = q
            else:
                eq.append(name)
        if eq:
            data = self._yf.download([f"{s}.NS" for s in eq], period="7d", interval="1d",
                                     group_by="ticker", auto_adjust=True, progress=False, threads=True)
            multi = isinstance(data.columns, pd.MultiIndex)
            for s in eq:
                try:
                    sub = data[f"{s}.NS"] if multi else data
                except KeyError:
                    continue
                q = self._quote_from_daily(sub)
                if q:
                    out[f"NSE:{s}"] = q
        return out

    def _ensure_candles(self, interval):
        if self._candles is not None:
            return
        self._candles = {}  # set first so a failed batch is not retried per-symbol
        mins = "".join(ch for ch in str(interval) if ch.isdigit()) or "5"
        data = self._yf.download([f"{s}.NS" for s in self.symbols],
                                 period=f"{self.lookback_days}d", interval=f"{mins}m",
                                 group_by="ticker", auto_adjust=True, progress=False, threads=True)
        multi = isinstance(data.columns, pd.MultiIndex)
        for s in self.symbols:
            try:
                sub = data[f"{s}.NS"] if multi else data
            except KeyError:
                continue
            self._candles[s] = sub

    def historical(self, token, start, end, interval):
        self._ensure_candles(interval)
        sub = self._candles.get(token)
        if sub is None or sub.empty:
            return []
        df = sub.dropna(subset=["Close"]).reset_index()
        tcol = "Datetime" if "Datetime" in df.columns else df.columns[0]
        ts = pd.to_datetime(df[tcol])
        if ts.dt.tz is not None:  # yfinance intraday is tz-aware IST; make tz-naive IST
            ts = ts.dt.tz_convert(TZ).dt.tz_localize(None)
        return pd.DataFrame({
            "date": ts, "open": df["Open"], "high": df["High"],
            "low": df["Low"], "close": df["Close"], "volume": df["Volume"],
        }).to_dict("records")

    def _ensure_daily(self):
        if self._daily is not None:
            return
        self._daily = {}  # set first so a failed batch is not retried per-symbol
        data = self._yf.download([f"{s}.NS" for s in self.symbols], period="3mo",
                                 interval="1d", group_by="ticker", auto_adjust=True,
                                 progress=False, threads=True)
        multi = isinstance(data.columns, pd.MultiIndex)
        for s in self.symbols:
            try:
                self._daily[s] = data[f"{s}.NS"] if multi else data
            except KeyError:
                continue

    def daily_trend(self, symbol):
        """Higher-timeframe trend from ~3 months of daily candles: price vs its
        20-day SMA plus the SMA's slope. Returns 'UP' / 'DOWN' / 'FLAT'."""
        self._ensure_daily()
        sub = self._daily.get(symbol)
        if sub is None:
            return "FLAT"
        close = sub["Close"].dropna()
        if len(close) < 25:
            return "FLAT"
        sma20 = close.rolling(20).mean()
        last, now, prev = float(close.iloc[-1]), float(sma20.iloc[-1]), float(sma20.iloc[-5])
        if last > now and now > prev:
            return "UP"
        if last < now and now < prev:
            return "DOWN"
        return "FLAT"


class NSESource:
    """Hybrid free source: Yahoo for the full-universe liquidity snapshot and candle
    history, NSE's own public JSON API overlaid for the pieces Yahoo can't give.

    What NSE adds for free (verified against www.nseindia.com, Oct 2025):
      * LIVE index levels (allIndices) -> a fresher regime read than Yahoo's
        ~15-min-delayed daily index bar.
      * Market/holiday state (marketStatus) -> real open/closed, not just a
        weekend check.
      * During the PRE-OPEN window (09:00-09:15): real opening gaps and pre-open
        order-book imbalance (buy/sell quantity) for every stock, overlaid onto the
        Yahoo snapshot. This is exactly where the best intraday setups are framed.

    What NSE no longer gives free scripts (so we honestly fall back to Yahoo):
      * The old bulk constituents feed (equity-stockIndices) is removed (404) and
        per-symbol quote-equity is anti-bot blocked (403). There is therefore no
        free full-universe INTRADAY quote; mid-session equity quotes come from
        Yahoo (~15 min delayed). Candles and daily trend always come from Yahoo.

    NSE fingerprints TLS and blocks plain requests, so this uses curl_cffi with
    browser impersonation (already a dependency via yfinance). Every call degrades
    to empty on error rather than crashing the scan.
    """
    BASE = "https://www.nseindia.com"

    def __init__(self, lookback_days=5):
        from curl_cffi import requests as cr
        self._yahoo = YahooSource(lookback_days)  # universe + snapshot + candles + trend
        self.symbols = self._yahoo.symbols
        self._session = cr.Session(impersonate="chrome")
        self._session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/",
        })
        self._preopen = None  # lazy {symbol: {last, prev, buy_qty, sell_qty}}
        self._idx = None      # lazy {index name: live row}
        self._prime()

    def _prime(self):
        try:
            self._session.get(self.BASE + "/", timeout=15)
        except Exception:
            pass

    def _api(self, path, params=None):
        for _ in range(2):  # one retry after re-priming cookies
            try:
                r = self._session.get(self.BASE + path, params=params, timeout=15)
                if r.status_code in (401, 403):
                    self._prime()
                    continue
                r.raise_for_status()
                return r.json()
            except Exception:
                self._prime()
        return {}

    def instruments(self, exchange="NSE"):
        return self._yahoo.instruments(exchange)

    def market_state(self):
        """'Open' / 'Close' / 'Pre-Open' for the Capital Market segment (or '')."""
        for s in (self._api("/api/marketStatus").get("marketState") or []):
            if str(s.get("market", "")).strip().lower() == "capital market":
                return str(s.get("marketStatus", "")).strip()
        return ""

    def _load_preopen(self):
        if self._preopen is not None:
            return
        self._preopen = {}
        for row in (self._api("/api/market-data-pre-open", {"key": "ALL"}).get("data") or []):
            meta = row.get("metadata") or {}
            sym = str(meta.get("symbol", "")).strip().upper()
            if not sym:
                continue
            legs = ((row.get("detail") or {}).get("preOpenMarket") or {}).get("preopen") or []
            buy = sum(float(l.get("buyQty") or 0) for l in legs)
            sell = sum(float(l.get("sellQty") or 0) for l in legs)
            self._preopen[sym] = {"last": float(meta.get("lastPrice") or 0),
                                  "prev": float(meta.get("previousClose") or 0),
                                  "buy_qty": buy, "sell_qty": sell}

    def _load_idx(self):
        if self._idx is not None:
            return
        data = self._api("/api/allIndices").get("data") or []
        self._idx = {str(r.get("index", "")).strip().upper(): r for r in data}

    def quote(self, keys):
        idx_keys = [k for k in keys if k.split(":", 1)[-1] in YAHOO_INDEX]
        eq_keys = [k for k in keys if k.split(":", 1)[-1] not in YAHOO_INDEX]
        out = {}

        # Indices: live levels from NSE, fall back to Yahoo if NSE is unreachable.
        if idx_keys:
            self._load_idx()
            missing = []
            for k in idx_keys:
                r = self._idx.get(k.split(":", 1)[-1].upper())
                if not r or not float(r.get("last") or 0):
                    missing.append(k)
                    continue
                out[k] = {
                    "last_price": float(r.get("last") or 0), "volume": 0, "average_price": None,
                    "ohlc": {"open": float(r.get("open") or 0), "high": float(r.get("high") or 0),
                             "low": float(r.get("low") or 0), "close": float(r.get("previousClose") or 0)},
                    "buy_quantity": 0, "sell_quantity": 0, "depth": {},
                }
            if missing:
                out.update(self._yahoo.quote(missing))

        # Equities: Yahoo supplies the liquidity snapshot (turnover/volume) always;
        # in the pre-open window, overlay NSE's real gap price and order imbalance.
        if eq_keys:
            out.update(self._yahoo.quote(eq_keys))
            if self.market_state().lower().startswith("pre"):
                self._load_preopen()
                for k in eq_keys:
                    base = out.get(k)
                    po = self._preopen.get(k.split(":", 1)[-1].upper())
                    if not base or not po or po["last"] <= 0:
                        continue
                    base["last_price"] = po["last"]
                    if po["prev"] > 0:
                        base["ohlc"]["close"] = po["prev"]
                    base["buy_quantity"], base["sell_quantity"] = po["buy_qty"], po["sell_qty"]
        return out

    def historical(self, token, start, end, interval):
        return self._yahoo.historical(token, start, end, interval)

    def daily_trend(self, symbol):
        return self._yahoo.daily_trend(symbol)

    def announcements(self, days=1):
        """Real NSE corporate filings for the last `days` as {SYMBOL: [ann, ...]}.

        Each ann = {"desc": category, "text": filing text}. Symbol-specific (NSE
        tags every filing to one listed company), so matching catalyst keywords in
        the text can't be tripped by an unrelated company's news — unlike a web
        news search. Empty dict on any error.
        """
        to = datetime.now(TZ).strftime("%d-%m-%Y")
        frm = (datetime.now(TZ) - timedelta(days=max(days, 1))).strftime("%d-%m-%Y")
        data = self._api("/api/corporate-announcements",
                         {"index": "equities", "from_date": frm, "to_date": to})
        rows = data if isinstance(data, list) else (data.get("data") or [])
        out = {}
        for r in rows:
            sym = str(r.get("symbol", "")).strip().upper()
            if not sym:
                continue
            out.setdefault(sym, []).append({
                "desc": str(r.get("desc") or "").strip(),
                "text": str(r.get("attchmntText") or "").strip()})
        return out


class MockSource:
    """Deterministic synthetic data for offline verification.

    Produces a fixed universe of liquid-looking names with a seeded random walk,
    so every run yields the same candles and the same ranked report.
    """
    _SYMBOLS = ["RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "INFY", "TCS",
                "TATAMOTORS", "BEL", "HAL", "ZOMATO", "TRENT", "DLF",
                "NIFTY 50", "NIFTY BANK"]

    def __init__(self, lookback_days=5):
        self.lookback_days = lookback_days
        self._rng = np.random.default_rng(42)
        self._rows = self._build_instruments()

    def _build_instruments(self):
        rows = []
        for i, sym in enumerate(self._SYMBOLS):
            rows.append({"tradingsymbol": sym, "instrument_token": 100000 + i,
                         "segment": "NSE", "instrument_type": "EQ",
                         "exchange": "NSE", "name": sym})
        return rows

    def instruments(self, exchange="NSE"):
        return self._rows

    def _series(self, token):
        """Build a multi-day 5-minute series as a DataFrame for one token.

        To exercise the full candidate path offline, each token carries a
        persistent drift (even tokens trend up, odd trend down) and the latest
        session's volume is elevated so time-adjusted RVOL clears the gate.
        """
        seed = self._rng if token is None else np.random.default_rng(token)
        base = 100 + (token % 50) * 10 if token else 100
        drift = 0.0010 if (token or 0) % 2 == 0 else -0.0010  # persistent trend
        # Market hours 09:15-15:30 IST -> 75 five-minute candles per session.
        per_day = 75
        bars = []
        price = base
        # Build sessions on consecutive weekdays ending "today".
        day = datetime(2024, 1, 8, 9, 15)  # a Monday; fixed for determinism
        for d in range(self.lookback_days):
            start = day + timedelta(days=d)
            vol_boost = 2.2 if d == self.lookback_days - 1 else 1.0  # latest-session surge
            for b in range(per_day):
                ts = start + timedelta(minutes=5 * b)
                ret = seed.normal(drift, 0.0012)
                o = price
                c = o * (1 + ret)
                h = max(o, c) * (1 + abs(seed.normal(0, 0.0008)))
                l = min(o, c) * (1 - abs(seed.normal(0, 0.0008)))
                vol = int(abs(seed.normal(50000, 15000)) * (1.5 if b < 6 else 1.0) * vol_boost)
                bars.append({"date": ts, "open": o, "high": h, "low": l,
                             "close": c, "volume": vol})
                price = c
        return pd.DataFrame(bars)

    def historical(self, token, start, end, interval):
        return self._series(token).to_dict("records")

    def quote(self, keys):
        out = {}
        token_by_sym = {r["tradingsymbol"]: r["instrument_token"] for r in self._rows}
        for key in keys:
            sym = key.split(":", 1)[-1]
            token = token_by_sym.get(sym)
            if token is None:
                continue
            df = self._series(token)
            today = df[df["date"].dt.date == df["date"].iloc[-1].date()]
            prev = df[df["date"].dt.date != df["date"].iloc[-1].date()]
            prev_close = float(prev["close"].iloc[-1]) if not prev.empty else float(today["open"].iloc[0])
            last = float(today["close"].iloc[-1])
            day_vol = int(today["volume"].sum())
            # Synthetic depth: tight 2-paise spread, modest imbalance.
            spread = last * 0.0003
            out[key] = {
                "last_price": last,
                "volume": day_vol,
                "average_price": float((today["high"] + today["low"] + today["close"]).div(3).mean()),
                "ohlc": {"open": float(today["open"].iloc[0]),
                         "high": float(today["high"].max()),
                         "low": float(today["low"].min()),
                         "close": prev_close},
                "buy_quantity": 120000,
                "sell_quantity": 90000,
                "depth": {"buy": [{"price": last - spread, "quantity": 5000}],
                          "sell": [{"price": last + spread, "quantity": 5000}]},
            }
        return out


def get_source(name, lookback_days=5):
    """Select a data source by name: 'mock', 'yahoo', 'nse', 'kite' or 'dhan'."""
    name = (name or "kite").lower()
    if name == "mock":
        return MockSource(lookback_days)
    if name == "nse":
        return NSESource(lookback_days)
    if name == "dhan":
        return DhanSource()
    if name in ("yahoo", "yfinance", "free"):
        return YahooSource(lookback_days)
    return KiteSource()
