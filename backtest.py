"""Backtest the intraday screen on FREE historical data — decide with evidence,
before risking any money, whether it shows a positive edge after Indian costs.

It replays the live selection pipeline point-in-time on each past session using
Yahoo (yfinance) 5-minute candles, picks that day's TOP-5 exactly like bot.py,
simulates the trigger -> stop/target fills intraday, and tallies realized P&L net
of the cost model in costs.py.

WHAT IS AND ISN'T TESTED
  Tested faithfully: liquidity + technical gates, same-day range/gap filters,
  daily-trend alignment (computed as-of each past day, no lookahead), and the
  per-day top-5 ranking.
  NOT tested: the live NEWS boost. fetch_live_news only returns *today's*
  headlines; point-in-time historical news isn't available free, so the catalyst
  component is held NEUTRAL here. The news edge can only be proven by forward
  (paper) testing, never by this backtest.

Honesty notes (read them):
  * Planning aid, not advice. YOU decide to trade; it never tells you to.
  * Backtests are optimistic: they assume fills exactly at trigger/stop/target.
    When one candle spans both stop and target, it is counted as a LOSS.
  * Free data is delayed and the universe (symbols.txt) is today's names
    (survivorship bias). Past performance does not predict the future.

Run: python backtest.py                 (60 days of 5m candles, decide at 10:15)
     python backtest.py --days 30 --decision 10:30
"""
import sys

import pandas as pd
from dotenv import load_dotenv

import bot
import costs
from datasource import YahooSource

load_dotenv()

MIN_PRIOR_SESSIONS = 2     # need a prior-day baseline for RVOL/levels
LOOKBACK_SESSIONS = 5      # prior sessions fed to the screen (matches the live bot)
MIN_TRADES = 30            # below this the sample is too small to conclude
MIN_PROFIT_FACTOR = 1.3    # gross profit / gross loss, net of costs


def _arg(flag, default):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def trend_asof(daily_df, day):
    """Daily trend as-of `day` using ONLY prior daily closes (no lookahead):
    price vs 20-day SMA + the SMA's slope. Mirrors YahooSource.daily_trend."""
    if daily_df is None:
        return "FLAT"
    close = daily_df["Close"].dropna()
    close = close[close.index.date < day]
    if len(close) < 25:
        return "FLAT"
    sma = close.rolling(20).mean()
    last, now, prev = float(close.iloc[-1]), float(sma.iloc[-1]), float(sma.iloc[-5])
    if last > now and now > prev:
        return "UP"
    if last < now and now < prev:
        return "DOWN"
    return "FLAT"


def _leg_net(side, entry, exit_price, q):
    if q <= 0:
        return 0.0
    buy, sell = (entry, exit_price) if side == "LONG" else (exit_price, entry)
    return (sell - buy) * q - costs.round_trip_cost(buy, sell, q)


def simulate_fill(side, entry, stop, t1, t2, qty, eval_rows):
    """Trade management: enter at trigger, book HALF at T1 and trail the rest with
    the stop moved to breakeven, aiming for T2. Lets winners run while protecting
    the booked gain. Returns (result, net_rupees). Stop is checked before target in
    the same candle (conservative)."""
    q1 = qty // 2
    filled, runner, cur_stop, legs = False, False, stop, []

    def finish():
        net = sum(_leg_net(side, entry, px, q) for px, q in legs)
        return ("WIN" if net > 0 else "LOSS"), net

    for c in eval_rows:
        high, low = float(c["high"]), float(c["low"])
        if not filled:
            if (side == "LONG" and high >= entry) or (side == "SHORT" and low <= entry):
                filled = True
            else:
                continue
        hit_stop = low <= cur_stop if side == "LONG" else high >= cur_stop
        hit_t1 = high >= t1 if side == "LONG" else low <= t1
        hit_t2 = high >= t2 if side == "LONG" else low <= t2
        if not runner:
            if hit_stop:
                legs.append((cur_stop, qty)); return finish()
            if hit_t1:
                legs.append((t1, q1)); cur_stop, runner = entry, True  # book half, trail rest
                if hit_t2:
                    legs.append((t2, qty - q1)); return finish()
        else:
            if hit_stop:  # stop now at breakeven
                legs.append((cur_stop, qty - q1)); return finish()
            if hit_t2:
                legs.append((t2, qty - q1)); return finish()
    if not filled:
        return "NOFILL", 0.0
    legs.append((float(eval_rows[-1]["close"]), (qty - q1) if runner else qty))  # EOD close
    return finish()


def collect_candidates(days, decision):
    """Point-in-time screen per symbol/day. Returns {day: [candidate, ...]} where
    each candidate carries its as-of daily trend and the post-decision candles."""
    src = YahooSource(lookback_days=days)
    src._ensure_daily()  # prime the daily cache once for trend lookups
    by_day = {}
    print(f"Backtesting {len(src.symbols)} symbols over ~{days}d of 5m candles, "
          f"decision at {decision} IST...\n")

    for n, sym in enumerate(src.symbols, 1):
        candles = src.historical(sym, None, None, "5minute")
        if not candles:
            continue
        df = pd.DataFrame(candles)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        df["d"] = df["date"].dt.date
        df["t"] = df["date"].dt.strftime("%H:%M")
        daily_df = src._daily.get(sym)
        days_list = sorted(df["d"].unique())

        for i in range(MIN_PRIOR_SESSIONS, len(days_list)):
            day = days_list[i]
            prior = days_list[max(0, i - LOOKBACK_SESSIONS):i]
            slice_df = df[(df["d"].isin(prior)) |
                          ((df["d"] == day) & (df["t"] <= decision))]
            eval_df = df[(df["d"] == day) & (df["t"] > decision)]
            if eval_df.empty or slice_df[slice_df["d"] == day].empty:
                continue
            prev_rows = slice_df[slice_df["d"] < day]
            if prev_rows.empty:
                continue
            last_price = float(slice_df["close"].iloc[-1])
            prev_close = float(prev_rows["close"].iloc[-1])
            day_vol = float(slice_df[slice_df["d"] == day]["volume"].sum())
            snap_row = {
                "last_price": last_price,
                "day_change_pct": (last_price - prev_close) / prev_close * 100 if prev_close else 0.0,
                "turnover": last_price * day_vol,
                "buy_qty": 0.0, "sell_qty": 0.0, "spread_pct": 0.0,
            }
            cand = bot.score_candidate(
                sym, snap_row,
                slice_df[["date", "open", "high", "low", "close", "volume"]],
                nifty_change=0.0, regime="BACKTEST", offline=True, news={})
            if not cand:
                continue
            cand["_trend"] = trend_asof(daily_df, day)
            cand["_eval"] = eval_df[["high", "low", "close"]].to_dict("records")
            by_day.setdefault(str(day), []).append(cand)
        if n % 25 == 0:
            print(f"  ...scanned {n}/{len(src.symbols)} symbols")
    return by_day


def select_and_simulate(by_day):
    """Replay the live per-day selection: shortlist by score, apply daily-trend
    alignment (news held neutral), re-rank, take top-5, simulate their fills."""
    shortlist_n = bot.env_int("NEWS_SCAN_LIMIT", 15)
    top_n = bot.env_int("MAX_CANDIDATES", 2)
    trades = []
    for day, cands in by_day.items():
        cands.sort(key=lambda r: r["score"], reverse=True)
        short = cands[:shortlist_n]
        for r in short:
            # News is neutral in backtest (no point-in-time headlines). Apply the
            # same daily-trend adjustment the live bot does.
            if ((r["_trend"] == "UP" and r["side"] == "LONG") or
                    (r["_trend"] == "DOWN" and r["side"] == "SHORT")):
                r["components"]["price_structure"] = bot._clamp(r["components"]["price_structure"] + 20)
            elif ((r["_trend"] == "UP" and r["side"] == "SHORT") or
                  (r["_trend"] == "DOWN" and r["side"] == "LONG")):
                r["components"]["price_structure"] = bot._clamp(r["components"]["price_structure"] - 30)
            r["score"] = sum(r["components"][k] * bot.WEIGHTS[k] for k in bot.WEIGHTS) / sum(bot.WEIGHTS.values())
        # With-trend only: drop setups that fight a clear daily trend.
        short = [r for r in short if not (
            (r["_trend"] == "UP" and r["side"] == "SHORT") or
            (r["_trend"] == "DOWN" and r["side"] == "LONG"))]
        short.sort(key=lambda r: r["score"], reverse=True)
        for r in short[:top_n]:
            result, net = simulate_fill(r["side"], r["entry"], r["stop"],
                                        r["target1"], r["target2"], r["qty"], r["_eval"])
            if result == "NOFILL":
                continue
            trades.append({"symbol": r["symbol"], "day": day, "side": r["side"],
                           "result": result, "net": net})
    return trades


def report(trades, days, decision):
    capital = bot.env_float("TRADING_CAPITAL_INR", 100000)
    n = len(trades)
    print("\n" + "=" * 64)
    print(f"BACKTEST RESULT  —  ~{days}d, decision {decision} IST, free delayed data")
    print("(per-day TOP-5 like bot.py; technicals+range+gap+daily-trend tested; "
          "NEWS held neutral — not backtestable free)")
    print("=" * 64)
    if n == 0:
        print("No trades were triggered. Nothing to evaluate — loosen gates or widen the window.")
        return

    wins = [t for t in trades if t["net"] > 0]
    losses = [t for t in trades if t["net"] <= 0]
    gross_profit = sum(t["net"] for t in wins)
    gross_loss = -sum(t["net"] for t in losses)
    total_net = gross_profit - gross_loss
    pf = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    win_rate = len(wins) / n * 100
    expectancy = total_net / n

    print(f"Trades taken:     {n}  (net wins {len(wins)}, losses {len(losses)}; "
          f"exits: half@T1 + runner to T2)")
    print(f"Win rate (net):   {win_rate:.1f}%")
    print(f"Net P&L:          Rs {total_net:,.0f}  on Rs {capital:,.0f} capital "
          f"({total_net / capital * 100:+.1f}% over the period)")
    print(f"Profit factor:    {pf:.2f}   (net profit / net loss; >1 makes money)")
    print(f"Expectancy/trade: Rs {expectancy:,.0f}  (after Indian costs + slippage)")

    print("\n" + "-" * 64)
    enough = n >= MIN_TRADES
    edge = total_net > 0 and pf >= MIN_PROFIT_FACTOR and expectancy > 0
    if not enough:
        print(f"VERDICT: ⚠️  NOT ENOUGH EVIDENCE — only {n} trades (need >= {MIN_TRADES}).")
        print("Do NOT put real money yet. Widen --days or keep forward-testing.")
    elif edge:
        print("VERDICT: ✅  POSITIVE edge after costs — even WITHOUT the news boost.")
        print("This is EVIDENCE, not a green light. Before real money: forward-test it")
        print("live on delayed data for a few weeks (that also proves the news edge),")
        print("then start with the SMALLEST size. Backtests overstate reality.")
    else:
        print(f"VERDICT: ❌  NO proven edge after costs (PF {pf:.2f}, net Rs {total_net:,.0f}).")
        print("Note: the news boost is NOT in this number and could help — but that is")
        print("unproven. Do NOT put real money on this; refine and forward-test.")
    print("-" * 64)
    print("Reminder: optimistic fills, delayed/survivorship data, news not tested, "
          "past != future, not SEBI-registered advice. The decision to risk money is yours.")


if __name__ == "__main__":
    d = int(_arg("--days", "60"))
    dec = _arg("--decision", "10:15")
    report(select_and_simulate(collect_candidates(d, dec)), d, dec)
