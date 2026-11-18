"""A discretionary trader's read for one stock — the "open a chart and know the
levels" view, as plain text you can read in the terminal or push to your phone.

Composes the existing engine (analyze.analyze for trend/RS/liquidity/setup-odds +
levels.ladder for the structural map) into a single framed read: where price sits
in the level ladder, the nearest support/resistance, and an honest entry/stop/
target with its real reward:risk. It does NOT predict — it frames. Everything
shown is deterministic and sourced from free data.

Usage:  python read.py RELIANCE
"""
import sys

import indicators as ind
import levels as lv
import analyze as an


def _ladder_view(ladder, price, side_support, side_resist):
    """Render the ladder with a price marker, resistances above / supports below."""
    lines = []
    above = [l for l in ladder if l["price"] > price]
    below = [l for l in ladder if l["price"] <= price]
    for l in reversed(above):                     # highest first
        lines.append(f"   ▲ {l['price']:>9.2f}  {l['label']}")
    lines.append(f"   ── price {price:>6.2f} ──")
    for l in reversed(below):                     # nearest support first
        lines.append(f"   ▼ {l['price']:>9.2f}  {l['label']}")
    return "\n".join(lines)


def read(symbol):
    sym = symbol.upper().strip()
    r = an.analyze(sym)
    ev = r.get("evidence") or {}
    price = r.get("price")
    if price is None:
        return f"{sym}: {r.get('note','no data')}"

    # Full structural ladder (analyze only includes it on a BUY/SELL; recompute so a
    # HOLD still shows the map — the map is useful even when there's no signal).
    m5 = ind.prepare(an._hist(sym, "15d", "5m"))
    atr = ind.atr(m5) or price * 0.003
    ladder = lv.ladder(m5)
    sup = lv.nearest_support(ladder, price, 1)
    res = lv.nearest_resistance(ladder, price, 1)
    sup_p = sup[0] if sup else None
    res_p = res[0] if res else None

    liq = ev.get("liquidity") or {}
    sb = ev.get("setup_backtest") or {}
    rs = ev.get("rel_strength_vs_nifty_pct")

    out = []
    rs_txt = (f"RS {rs:+.2f}% vs NIFTY " + ("(outperforming)" if rs and rs > 0 else "(lagging)" if rs is not None else "")) if rs is not None else "RS n/a"
    out.append(f"{sym}  ₹{price:.2f}   {rs_txt}")
    out.append(f"Trend: 5m {ev.get('intraday_trend_5m','?')} · daily {ev.get('daily_trend','?')} · RSI {ev.get('rsi','?')} · VWAP ₹{ev.get('vwap','?')}")
    thin = liq.get("thin")
    liq_txt = f"₹{liq.get('turnover_cr_today','?')}cr today · RVOL {liq.get('rvol','?')}"
    out.append("Liquidity: " + liq_txt + ("  ⚠ THIN — hard to fill at size" if thin else "  — OK"))
    if sb.get("sessions"):
        out.append(f"Recent ORB setup ({sb['sessions']} sessions): win {sb['win_rate_pct']}% · exp {sb['expectancy_rs_per_10qty']}/10qty"
                   + ("  ← poor odds" if sb.get('expectancy_rs_per_10qty', 0) <= 0 else "  ← positive"))

    out.append("\nLevels (▲ resistance / ▼ support):")
    out.append(_ladder_view(ladder, price, sup_p, res_p))

    # Options OI walls (free Upstox API) — where writers sit is the S/R traders watch.
    # Degrades silently if the token is absent or the stock has no F&O.
    try:
        import oi as oimod
        w = oimod.walls(sym)
    except Exception:
        w = None
    if w and w.get("call_wall") and w.get("put_wall"):
        out.append(f"\nOptions OI (exp {w['expiry']}): PCR {w['pcr']} · max-pain ₹{w['max_pain']:.0f}")
        out.append(f"   ▲ call wall ₹{w['call_wall']:.0f} (OI {w['call_wall_oi']:,.0f})  = resistance")
        out.append(f"   ▼ put wall  ₹{w['put_wall']:.0f} (OI {w['put_wall_oi']:,.0f})  = support")

    # Framing: the pullback-to-support long — buy AT the nearest support, stop just
    # below it, target the nearest resistance. R:R is measured from that entry (not
    # the current price), so it's internally consistent. Framing, not a call.
    if sup_p and res_p:
        entry = sup_p["price"]
        stop = entry - 0.4 * atr
        reward = res_p["price"] - entry
        risk = entry - stop
        rr = reward / risk if risk > 0 else 0
        out.append(f"\nFraming (pullback long): buy ▼{entry:.2f} ({sup_p['label']}), "
                   f"stop {stop:.2f}, target ▲{res_p['price']:.2f} ({res_p['label']}) "
                   f"→ R:R ≈ {rr:.2f}" + ("  ✓ asymmetric" if rr >= 1.5 else "  ✗ thin"))
    out.append(f"\nVerdict: {r.get('action','?')}  — {r.get('note','')}")
    out.append("(Framing, not a prediction. Paper-trade; honour the stop. No proven edge yet — see audit.py.)")
    return "\n".join(out)


if __name__ == "__main__":
    syms = sys.argv[1:] or ["RELIANCE"]
    for s in syms:
        print(read(s))
        print("\n" + "=" * 60 + "\n")
