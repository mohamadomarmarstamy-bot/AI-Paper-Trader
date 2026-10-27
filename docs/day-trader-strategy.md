# Intraday strategy experiment v1

The main scanner uses daily history for discovery. A daily technical grade does
not establish an intraday entry. This release adds a usable, owner-triggered
minute-data experiment panel in Scanner. It does not replace the running PAPER
entry/exit algorithm, change Pro Ticker submissions, or close existing holdings.
No broker write capability is passed into the experiment module.

## Research and implementation choices

- Zarattini, Barbon and Aziz, *A Profitable Day Trading Strategy for the U.S.
  Equity Market* (2024), studies opening-range breakouts and selecting stocks
  with unusually high opening activity. Its historical results are not a promise
  or an independent validation of this implementation. University-hosted paper:
  https://www.alexandria.unisg.ch/server/api/core/bitstreams/3c2989c4-688d-4d78-8a71-f02690990d51/content
- Bailey et al., *The Probability of Backtest Overfitting* (2015), explains why
  selecting among many historical configurations can produce false discoveries.
  This release freezes rules and decisions before subsequent outcomes, records
  no fitted model, and never automatically promotes a strategy:
  https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf
- Alpaca documents IEX as a single-exchange feed, distinct from consolidated
  SIP. All history and quote requests explicitly use the configured existing
  feed. No subscription or entitlement probe occurs:
  https://docs.alpaca.markets/us/docs/historical-stock-data-1
- Alpaca minute bars are timestamped aggregates. Pagination is explicit and
  bounded; incomplete pages cannot silently become complete history:
  https://docs.alpaca.markets/us/reference/stockbars

The VWAP pullback rule, 1.5 opening-RVOL threshold, chase/distance limits, risk
caps, cost assumptions and 2R target below are **our fixed experimental choices**,
not parameter values proven optimal by those sources. This is not a paper
replication, especially on sparse IEX microcap data.

## Frozen rules

Long-only, regular sessions, using the exchange calendar including early closes.
Evaluate only completed, ordered, unique minute bars. Current-session history
must start at the open and be gap-free, with its last completion within 120
seconds. Require a same-feed uncrossed bid/ask no more than 30 seconds old.

Opening RVOL is the first five minutes' observed volume divided by the average
of 14 prior five-minute openings on the same feed. Missing history is unavailable,
never zero or full-market RVOL. Session VWAP uses typical price times observed
minute volume. Neither represents the consolidated market when the feed is IEX.

Two independent hypotheses:

1. Bullish five-minute opening range; the latest completed close crosses its
   high from below, closes above session VWAP, and opening RVOL is at least 1.5.
   Stop at the opening low.
2. At least 21 session minutes; session VWAP rises relative to three bars ago;
   a recent low touches within 0.2% above VWAP; the latest close exceeds VWAP and
   the previous bar high with volume at least the prior 20-bar mean. Same opening
   RVOL condition. Stop at the last three bars' low.

Reject a quote more than 0.5% above the signal close, or a technical stop outside
0.2–5% below the reference ask. Planned whole shares fit both $2,000 notional and
$20 risk, including the base modeled friction. Targets are 2 times gross price
risk. These are simulation caps, not changes to account settings. Actual losses
can exceed planned risk due to gaps and liquidity.

Freeze before the final 30 session minutes. Expire no later than 60 minutes or
five minutes before that session's close. The simulated minute path begins at
the next minute boundary after capture; the intervening blind interval is
reported. No same-minute historical wick can decide a new trade's outcome.

## Outcomes and practical limits

Owner clicks **Freeze intraday experiment**, then **Evaluate saved forward
outcome** after the displayed deadline. Pending clicks report their due time.
No background jobs or broker orders are started by this panel. Request identity
is preserved on retries. One observation per symbol/minute prevents duplicate
browser retries from multiplying samples. Completed modeled outcomes cannot be
overwritten. Missing paths remain retryable and are never counted as zero P/L.

Forward simulation assumes the captured ask plus per-side stress at entry,
exit price minus per-side stress, and $0.005/share/side modeled fees. Three fixed
stress levels are 10, 30 and 60 basis points per side. Stop gaps use the lower
open; bars touching stop and target use the stop first. A same-size buy-and-hold
control uses the same capture and expiry, under the same cost scenario.

OHLC cannot establish actual fills, spreads at exit, queue priority, partial
fills, halts, or slippage. These are modeled per-symbol research outcomes, not
broker trade P/L, executable price guarantees, or a capital-constrained portfolio
backtest. Overlapping strategies/symbols are correlated and selected symbols are
biased. Summary covers only the latest 200 observations and displays no-data
profits as unavailable. Sparse or revised provider data can invalidate results.

Before replacing automatic trading, collect prospective outcomes across varied
sessions, compare against the matched control and current policy, examine cost
sensitivity, drawdowns and concentration, then run a separately versioned PAPER
execution experiment. A good unit test or attractive short sample does not prove
an economic edge. No trading mode is automatically activated by these results.
