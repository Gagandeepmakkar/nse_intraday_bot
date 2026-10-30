"""Deterministic pre-trade risk gate for NSE intraday — the daily kill-switch.

Ported from the day-trader-skill risk manager, adapted to NSE: IST hours, no new
entries in the last ~30 min before the broker MIS auto-square-off, Indian costs in
the reward:risk test, and a daily-loss cap that halts further trades. Pure function,
no network, NO LLM in the decision (an LLM may explain a BLOCK, never override it) —
money-movement controls must be deterministic.

check(order) -> {verdict: PASS|BLOCK, reasons[], warnings[], ...}
record(order)  marks a trade taken today (feeds the daily-loss + trades-per-day caps)

State lives in risk_state.json keyed by IST date, so the caps reset each session.
"""
import os
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import costs

TZ = ZoneInfo(os.getenv("MARKET_TIMEZONE", "Asia/Kolkata"))
STATE = Path("risk_state.json")


def _f(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _cfg():
    capital = _f("TRADING_CAPITAL_INR", 100000)
    per_trade = _f("RISK_MAX_LOSS_PER_TRADE_INR",
                   capital * _f("RISK_PER_TRADE_PCT", 0.5) / 100)   # 0.5% -> ₹500
    return {
        "capital": capital,
        "max_loss_per_trade": per_trade,
        "max_daily_loss": _f("RISK_MAX_DAILY_LOSS_INR", per_trade * 3),  # 3 trades' worth
        "max_trades_per_day": int(_f("RISK_MAX_TRADES_PER_DAY", 3)),
        "min_net_rr": _f("MIN_NET_REWARD_RISK", 1.3),
        "max_position_pct": _f("RISK_MAX_POSITION_PCT", 100),  # bot sizes to <=100% cash; lower to add concentration control
        "no_entries_before": os.getenv("RISK_NO_ENTRIES_BEFORE", "09:15"),
        "no_entries_after": os.getenv("RISK_NO_ENTRIES_AFTER", "15:00"),
    }


def _today():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def _load():
    try:
        d = json.loads(STATE.read_text())
        return d if d.get("date") == _today() else {"date": _today(), "trades": []}
    except Exception:
        return {"date": _today(), "trades": []}


def _hhmm_now():
    return datetime.now(TZ).strftime("%H:%M")


def check(order, now_hhmm=None, weekday=None):
    """order = {symbol, side(LONG/SHORT), entry, stop, target, qty}. Deterministic."""
    c = _cfg()
    reasons, warnings = [], []
    side = str(order.get("side", "")).upper()
    entry, stop, target = float(order["entry"]), float(order["stop"]), float(order["target"])
    qty = int(order.get("qty", 0))
    long = side == "LONG"

    # 1. Level sanity
    if long and not (stop < entry < target):
        reasons.append("levels invalid for LONG (need stop < entry < target)")
    if not long and not (target < entry < stop):
        reasons.append("levels invalid for SHORT (need target < entry < stop)")
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0 or qty < 1:
        reasons.append("zero risk or zero quantity")
        return _verdict(order, reasons, warnings, c, None, None)

    # 2. Max loss per trade
    trade_max_loss = risk_per_share * qty
    if trade_max_loss > c["max_loss_per_trade"] * 1.001:
        reasons.append(f"max loss ₹{trade_max_loss:.0f} exceeds per-trade cap "
                       f"₹{c['max_loss_per_trade']:.0f}")

    # 3. Reward:risk NET of Indian costs (our edge over the source, which ignores costs)
    _, net_rr, _, _ = costs.net_reward_risk(side, entry, stop, target, qty)
    if net_rr < c["min_net_rr"]:
        reasons.append(f"net R:R {net_rr:.2f} below minimum {c['min_net_rr']:.2f} (after costs)")

    # 4. Position-size cap
    if entry * qty > c["capital"] * c["max_position_pct"] / 100:
        reasons.append(f"position ₹{entry*qty:.0f} exceeds {c['max_position_pct']:.0f}% of capital")

    # 5. Entry-time window (IST) + weekday
    hhmm = now_hhmm or _hhmm_now()
    wd = weekday if weekday is not None else datetime.now(TZ).weekday()
    if wd >= 5:
        reasons.append("market closed (weekend)")
    elif hhmm < c["no_entries_before"]:
        reasons.append(f"before entry window ({c['no_entries_before']} IST)")
    elif hhmm > c["no_entries_after"]:
        reasons.append(f"after entry cutoff ({c['no_entries_after']} IST — too close to square-off)")

    # 6. Daily caps (the kill-switch) — from today's recorded trades
    st = _load()
    taken = st["trades"]
    day_loss_committed = sum(t["max_loss"] for t in taken)
    if day_loss_committed + trade_max_loss > c["max_daily_loss"] * 1.001:
        reasons.append(f"daily-loss kill-switch: ₹{day_loss_committed:.0f} already at risk today, "
                       f"this adds ₹{trade_max_loss:.0f}, cap ₹{c['max_daily_loss']:.0f}")
    if len(taken) >= c["max_trades_per_day"]:
        reasons.append(f"max trades/day reached ({c['max_trades_per_day']})")
    if any(t["symbol"] == order.get("symbol") and t["side"] == side for t in taken):
        warnings.append(f"already took {order.get('symbol')} {side} today")

    return _verdict(order, reasons, warnings, c, net_rr, trade_max_loss)


def _verdict(order, reasons, warnings, c, net_rr, trade_max_loss):
    return {
        "symbol": order.get("symbol"), "side": str(order.get("side", "")).upper(),
        "verdict": "BLOCK" if reasons else "PASS",
        "reasons": reasons, "warnings": warnings,
        "net_rr": round(net_rr, 2) if net_rr is not None else None,
        "max_loss": round(trade_max_loss, 0) if trade_max_loss is not None else None,
        "daily_cap": c["max_daily_loss"], "per_trade_cap": c["max_loss_per_trade"],
    }


def record(order):
    """Mark a trade as taken today so the daily caps count it. Call when you act."""
    st = _load()
    st["trades"].append({
        "symbol": order.get("symbol"), "side": str(order.get("side", "")).upper(),
        "max_loss": abs(float(order["entry"]) - float(order["stop"])) * int(order.get("qty", 0)),
        "at": _hhmm_now(),
    })
    STATE.write_text(json.dumps(st, indent=1))
    return st


def reset():
    """Clear today's state (e.g. a new session)."""
    STATE.write_text(json.dumps({"date": _today(), "trades": []}, indent=1))


if __name__ == "__main__":
    import sys
    # risk.py SYMBOL SIDE ENTRY STOP TARGET QTY
    a = sys.argv[1:]
    if len(a) < 6:
        print("usage: python risk.py SYMBOL SIDE ENTRY STOP TARGET QTY")
        sys.exit(0)
    o = {"symbol": a[0], "side": a[1], "entry": a[2], "stop": a[3], "target": a[4], "qty": a[5]}
    print(json.dumps(check(o), indent=2))
