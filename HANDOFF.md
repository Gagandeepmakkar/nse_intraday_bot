# HANDOFF — NSE intraday bot (state, daily ops, and the honest earn plan)

Paste this whole file into a fresh session to continue. Working dir:
`/Users/gagandeep.singh/Downloads/nse_intraday_bot` · venv: `./.venv/bin/python`

> Scope: **NSE only.** Crypto work is parked for later (see memory `crypto-frequency-trap.md`
> and `extension_btc/` — the one validated edge is crypto daily Donchian, revisit separately).

---

## 0. The one rule everything obeys
Deterministic edge only. AI reads text (news/filings), it NEVER predicts price. Nothing goes
to real money until it survives a backtest AND forward paper-logging. If I'm chasing a losing
idea, I say so plainly — I don't sell hope.

---

## 1. What is PROVEN for NSE (so you know what's real)
| Idea | Verdict | Evidence |
|---|---|---|
| NSE intraday 6-factor scanner (VWAP/EMA/ORB/RSI/Supertrend/Volume) | ❌ **Negative-EV** | 60d backtest neg; live deduped ledger −0.75R / −0.09R exp / 50% win over 8 calls |
| **Early-RVOL continuation** (high first-30-min volume → rides to close) | ❌ **NO edge** (tested 18 Nov 2026) | Negative even GROSS: RVOL≥2x −9.6bps, ≥3x −4.6bps; RVOL does NOT sort continuation; 420 obs, 20 names, 44 days. Scanner NOT built |
| Catalyst + tape-gated mover selection | ❓ **Untested — the one worth trying** | Your own diagnosis. Only path = forward paper-log, do NOT build on faith |

**Bottom line:** there is **no proven NSE edge yet.** Everything NSE is **paper only** until a
specific setup clears forward validation. The system today is an honest *research + tracking*
tool, not a money-maker — and the audit layer keeps it honest.

---

## 2. What happens DAILY right now (automated, free infra)
Driven by `cron_run.sh` (macOS LaunchAgents `com.nse.intraday.scheduler` + `logserver`, both
running). Times are IST. Market-closed runs are auto no-ops.

| Time (IST) | What runs | What it does |
|---|---|---|
| every slot | keeps `serve_picks.py` alive (:8787) | so the extension can log every signal+outcome to `journal.jsonl` |
| every slot | `track.py` | grades yesterday's picks to outcomes |
| ~09:08–09:11 | `watchlist.py --morning` | today's ~5 hot stocks incl. pre-open gaps (catalyst-first) |
| ~09:11–09:15 | `briefing.py` | deep read on your BRIEF_SYMBOLS |
| 09:30–15:15 | `movers.py --ntfy` | scans NSE live top gainers/losers, ranks by move+stage+catalyst, pushes shortlist to phone |
| ≥15:35 | `audit.py --ntfy --email` | deduped self-audit + per-call P&L ledger (win%, R, expectancy) |
| ~18:00 | `watchlist.py --evening` | tomorrow's candidates from today's catalysts+movers |

**You receive:** phone (ntfy) + email alerts for the morning/evening watchlists, intraday
movers, and the post-close audit.
*(The scheduler also runs a crypto check each slot — ignore it for now, NSE is the focus.)*

---

## 3. How RESEARCH is done (the catalyst pipeline)
- **watchlist.py** — catalyst-FIRST. Finds stocks with a fresh catalyst (earnings, order win,
  M&A, approval, buyback, block deal) via NSE announcements + AI text classification (Gemini,
  free tier — text only). Tiers by impact (T1 strong … T3 routine). NOT yesterday's gainers.
  Writes `watchlist.json`. Runs morning (today) + evening (tomorrow).
- **movers.py** — pulls NSE's own live top gainers/losers, tags stage (early <4% / building
  4–7% / extended >7%), down-weights "extended" so you don't chase the top, adds any catalyst.
  LAGGING by nature (sees a stock after it's already moved 2–4%).
- **briefing.py** — focused pre-open read on a short symbol list.
- **news** (serve_picks `/news`) — same-day event check; genuine events (earnings/halt/circuit)
  veto a signal, general news only warns.
- **audit.py** — the honesty layer. Dedupes repeated logs, computes real expectancy/P&L per
  call. This is what caught the intraday scanner being negative.

---

## 4. How you RUN the NSE extension
`extension/` (Chrome):
1. `chrome://extensions` → Developer mode → Load unpacked → select `extension/`.
2. Open a stock on groww.in (equity chart, URL has `/CASH/<TICKER>/`).
3. Panel: Signals / VIM / Scanner modes + 🎯 Watchlist + 🔥 Movers sections.
4. Timeframe **5-min** for intraday signals, **1D/1W** for swing. It reads live candles+volume,
   fuses the catalyst side (news veto), logs every signal+outcome to `serve_picks.py`.
5. **Treat every NSE signal as PAPER** — the edge isn't proven. The point right now is to
   *log* calls so `audit.py` can tell you the truth over a real sample.

---

## 5. What I WANT: run daily-live to earn on NSE — with FREE infra only
**Free stack (no paid plan):** yfinance (NSE daily candles), Groww MCP **historical** candles
(FREE — no ₹499 plan), NSE gainers API, ntfy phone push, Gemini free tier (text only),
LaunchAgents/cron, the extension, local `serve_picks.py`. **The ₹499 Groww live-data plan is
NOT needed** (it was only for live RVOL streaming, which we killed — RVOL was a no-go).

**The honest NSE earn plan on free infra:**
1. **Today = research + discipline, PAPER only.** Use the stack (watchlist/movers/briefing) to
   know WHERE the fresh catalyst + volume is, open those in the extension for clean
   levels/stops, and **log them as paper.** No real money yet — there's no proven edge.
2. **Validate the ONE promising idea next: the catalyst-GATED setup.** Require a *real fresh
   catalyst* (not just price/volume) + confirmation, then track outcome. Forward paper-log it
   **2–3 months** with `audit.py`. This is the only NSE path with a real chance — pure
   early-RVOL already failed, so don't repeat price/volume-only signals.
3. **Go-live gate:** only after that paper record is **positive out-of-sample** do you put
   small real money on it — manual orders, honour the stop. Same discipline that killed the
   6-factor scanner and early-RVOL.

**So "earn daily on NSE with free infra" realistically means:** the infra is already free and
running; the missing piece was never infrastructure — it's a **validated edge.** The catalyst-
gated setup is the candidate; it earns real money only after it earns trust on paper.

---

## 6. Ground rules + security (do not break)
- Validate before building — plumbing ≠ edge. Build a scanner ONLY if a backtest shows edge.
- **Order placement stays MANUAL / human-gated.** Groww MCP = data + tracking ONLY, never
  auto-fire trades (money-safety / regulated-data policy).
- Never commit secrets. Gitignored, keep out of git: `.env`, `journal.jsonl`, `vim_status.json`,
  `watchlist.json`, `crypto_state.json`. Telegram token is local-only.
- Don't push/PR unless asked.

---

## 7. NEXT STEPS (NSE, in order)
1. **Keep logging paper calls** via the extension → let `audit.py` build a real sample.
2. **Design + forward paper-log the catalyst-gated setup** (real catalyst required); grade with
   `audit.py` for 2–3 months; OOS test before any real money.
3. Do **not** build the live RVOL scanner or buy the ₹499 plan — early-RVOL is a proven no-go.
4. New idea? Write the backtest FIRST, run the cost sweep, decide at the gate.

Memory files: `nse-bot-approach.md`, `crypto-frequency-trap.md` (crypto, parked),
`groww-mcp-rvol-plan.md` (RVOL no-go result). RVOL backtest scripts: `rvol_backtest.py`,
`rvol_backtest2.py` (session scratchpad).

**START A NEW SESSION BY SAYING:** "Read HANDOFF.md, then show me today's watchlist/movers and
the current paper P&L ledger from audit.py."
