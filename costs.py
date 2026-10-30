"""Indian equity intraday (MIS) transaction-cost model and net reward:risk.

All rates are configurable via environment variables. Defaults approximate a
discount-broker schedule (Zerodha-style) as of 2024-25 and are NOT guaranteed
current. Verify against your broker's live contract-note schedule before relying
on net figures. These are planning estimates, not exact fills.
"""
import os


def _pct(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _cfg():
    return {
        # Brokerage: percent of each leg's turnover, capped per order (rupees).
        "brokerage_pct": _pct("COST_BROKERAGE_PCT", 0.03),
        "brokerage_cap": _pct("COST_BROKERAGE_CAP_INR", 20.0),
        # STT: sell side only for intraday equity.
        "stt_pct": _pct("COST_STT_PCT", 0.025),
        # Exchange transaction charge: both legs (NSE equity).
        "txn_pct": _pct("COST_EXCHANGE_TXN_PCT", 0.00297),
        # SEBI turnover fee: both legs.
        "sebi_pct": _pct("COST_SEBI_PCT", 0.0001),
        # Stamp duty: buy side only.
        "stamp_pct": _pct("COST_STAMP_PCT", 0.003),
        # GST on (brokerage + txn + sebi).
        "gst_pct": _pct("COST_GST_PCT", 18.0),
        # Slippage assumption per leg (percent of turnover).
        "slippage_pct": _pct("COST_SLIPPAGE_PCT", 0.05),
    }


def round_trip_cost(buy_price, sell_price, qty, cfg=None):
    """Total estimated round-trip cost in rupees for a buy leg and a sell leg.

    Direction-agnostic: for a SHORT trade pass the lower/exit price as sell/buy
    appropriately via net_reward_risk, which handles side. STT is charged on the
    sell leg and stamp duty on the buy leg regardless of trade direction.
    """
    cfg = cfg or _cfg()
    buy_val = buy_price * qty
    sell_val = sell_price * qty
    brokerage = (min(cfg["brokerage_pct"] / 100 * buy_val, cfg["brokerage_cap"])
                 + min(cfg["brokerage_pct"] / 100 * sell_val, cfg["brokerage_cap"]))
    stt = cfg["stt_pct"] / 100 * sell_val
    txn = cfg["txn_pct"] / 100 * (buy_val + sell_val)
    sebi = cfg["sebi_pct"] / 100 * (buy_val + sell_val)
    stamp = cfg["stamp_pct"] / 100 * buy_val
    gst = cfg["gst_pct"] / 100 * (brokerage + txn + sebi)
    slippage = cfg["slippage_pct"] / 100 * (buy_val + sell_val)
    return brokerage + stt + txn + sebi + stamp + gst + slippage


def net_reward_risk(side, entry, stop, target, qty, cfg=None):
    """Return (gross_rr, net_rr, net_profit_at_target, net_loss_at_stop).

    net_rr subtracts round-trip costs from the winning leg and adds them to the
    losing leg, i.e. the executable reward:risk after costs and assumed slippage.
    """
    cfg = cfg or _cfg()
    gross_risk = abs(entry - stop) * qty
    gross_reward = abs(target - entry) * qty
    gross_rr = gross_reward / gross_risk if gross_risk > 0 else 0.0

    if side == "LONG":
        cost_win = round_trip_cost(entry, target, qty, cfg)
        cost_loss = round_trip_cost(entry, stop, qty, cfg)
    else:  # SHORT: enter by selling high, exit by buying lower
        cost_win = round_trip_cost(target, entry, qty, cfg)
        cost_loss = round_trip_cost(stop, entry, qty, cfg)

    net_profit = gross_reward - cost_win
    net_loss = gross_risk + cost_loss
    net_rr = net_profit / net_loss if net_loss > 0 else 0.0
    return gross_rr, net_rr, net_profit, net_loss
