"""Structural price levels — the ladder a discretionary trader reads off a chart.

A "buy at 579, target 595" call is not a prediction; it's an asymmetric bet placed
AT a real level with a target AT the next real level. analyze.py used to set targets
as arbitrary ATR distances (price + 1.5*ATR); this module assembles the actual
reference levels so a target means "prior-day high" or "CPR top", not "+1.5 ATR".

The levels themselves are computed by indicators.py (prev_day_hlc, central_pivot_range,
opening_range, session_vwap) — this is assembly + nearest-level lookup, not new math.
Pure functions over the same 5-minute OHLCV DataFrame indicators.py expects.
"""
import indicators as ind


def _round_step(price):
    """Psychological round-number spacing by price magnitude (traders watch ₹50s on
    a ₹1300 stock, ₹1s on a ₹40 stock)."""
    if price >= 5000:
        return 100.0
    if price >= 1000:
        return 50.0
    if price >= 300:
        return 10.0
    if price >= 100:
        return 5.0
    if price >= 20:
        return 1.0
    return 0.5


def round_levels(price, within_pct=3.0):
    """Nearest round numbers above and below `price`, within `within_pct`%."""
    step = _round_step(price)
    base = round(price / step) * step
    out, band = [], price * within_pct / 100
    k = -3
    while k <= 3:
        lv = base + k * step
        if lv > 0 and abs(lv - price) <= band:
            out.append(lv)
        k += 1
    return out


def ladder(df, merge_pct=0.15):
    """All structural levels for the latest session as a sorted list of
    {price, label}. Levels within `merge_pct`% of each other are merged (the
    first-listed label wins — earlier entries are the higher-conviction ones)."""
    df = ind.prepare(df)
    price = float(df["close"].iloc[-1])
    raw = []

    hlc = ind.prev_day_hlc(df)
    if hlc:  # prior-day structure first — the levels traders quote most
        raw += [(hlc["high"], "PDH"), (hlc["low"], "PDL"), (hlc["close"], "PDC")]
        cpr = ind.central_pivot_range(hlc)
        raw += [(cpr["pivot"], "pivot"),
                (cpr["cpr_top"], "CPR-top"), (cpr["cpr_bottom"], "CPR-bot"),
                (cpr["cam_h4"], "Cam-H4"), (cpr["cam_h3"], "Cam-H3"),
                (cpr["cam_l3"], "Cam-L3"), (cpr["cam_l4"], "Cam-L4")]

    orb = ind.opening_range(df)
    if orb:
        raw += [(orb["high"], "ORB-high"), (orb["low"], "ORB-low")]

    raw.append((ind.session_vwap(df), "VWAP"))
    raw += [(lv, "round") for lv in round_levels(price)]

    raw = [(round(p, 2), lab) for p, lab in raw if p and p > 0]
    raw.sort(key=lambda x: x[0])
    merged = []
    for p, lab in raw:
        if merged and abs(p - merged[-1]["price"]) <= merged[-1]["price"] * merge_pct / 100:
            # Keep the stronger (earlier-listed) label but note the confluence.
            if lab not in merged[-1]["label"]:
                merged[-1]["label"] += "+" + lab
            continue
        merged.append({"price": p, "label": lab})
    return merged


def nearest_resistance(levels, price, n=2):
    """The n nearest levels strictly above price (asc)."""
    above = [l for l in levels if l["price"] > price * 1.0005]
    return above[:n]


def nearest_support(levels, price, n=2):
    """The n nearest levels strictly below price (desc — closest first)."""
    below = [l for l in levels if l["price"] < price * 0.9995]
    return list(reversed(below))[:n]


def snap_targets(df, side, entry, atr, fallback_mult=(1.5, 2.5)):
    """Structural targets for a trade. LONG -> nearest two resistances above entry;
    SHORT -> nearest two supports below. A level must be at least 0.4*ATR away to
    count as a target (a level ₹1 away is not a target), and falls back to ATR
    multiples when no structural level is in range (so a target is always defined).
    Returns {target1, target2, t1_label, t2_label, support, resistance, ladder}."""
    lv = ladder(df)
    min_dist = 0.4 * atr
    if side == "LONG":
        ts = [l for l in lv if l["price"] >= entry + min_dist][:2]
        t1 = ts[0]["price"] if ts else round(entry + fallback_mult[0] * atr, 2)
        t2 = ts[1]["price"] if len(ts) > 1 else round(entry + fallback_mult[1] * atr, 2)
        l1 = ts[0]["label"] if ts else f"+{fallback_mult[0]}ATR"
        l2 = ts[1]["label"] if len(ts) > 1 else f"+{fallback_mult[1]}ATR"
    else:
        ts = [l for l in reversed(lv) if l["price"] <= entry - min_dist][:2]
        t1 = ts[0]["price"] if ts else round(entry - fallback_mult[0] * atr, 2)
        t2 = ts[1]["price"] if len(ts) > 1 else round(entry - fallback_mult[1] * atr, 2)
        l1 = ts[0]["label"] if ts else f"-{fallback_mult[0]}ATR"
        l2 = ts[1]["label"] if len(ts) > 1 else f"-{fallback_mult[1]}ATR"
    sup = nearest_support(lv, entry, 1)
    res = nearest_resistance(lv, entry, 1)
    return {
        "target1": t1, "target2": t2, "t1_label": l1, "t2_label": l2,
        "support": sup[0] if sup else None,
        "resistance": res[0] if res else None,
        "ladder": lv,
    }


if __name__ == "__main__":
    import sys, json
    import yfinance as yf
    import pandas as pd
    sym = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    h = yf.Ticker(f"{sym}.NS").history(period="15d", interval="5m").reset_index()
    tcol = "Datetime" if "Datetime" in h.columns else h.columns[0]
    ts = pd.to_datetime(h[tcol])
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df = pd.DataFrame({"date": ts, "open": h["Open"], "high": h["High"],
                       "low": h["Low"], "close": h["Close"], "volume": h["Volume"]}).dropna()
    price = float(df["close"].iloc[-1])
    print(f"{sym}  price ₹{price:.2f}")
    print(json.dumps({"ladder": ladder(df),
                      "long_targets": snap_targets(df, "LONG", price, ind.atr(df))},
                     indent=2, default=str))
