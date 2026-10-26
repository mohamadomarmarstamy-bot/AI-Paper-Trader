# Evidence research and performance analytics

October 26, 2026 development update. This document describes implemented behavior,
not a production release approval or actual PAPER broker certification.

## Saved research

New manual reviews in main and isolated staging capture an additional immutable
`research_context` inside the existing grade JSON. Legacy grades/drafts and their
risk/admission rules are unchanged. Existing reviews are not backfilled or regraded.

The report replays entirely from a saved, versioned envelope and observation time.
It includes a SHA-256 digest, Alpaca reference provenance, up to 130 completed Yahoo
OHLCV sessions, SMA20/50, five-session momentum, completed-session relative volume,
dollar liquidity, ATR14, a prior 20-session high, bullish/bearish reasons and
contradictory evidence. SPY completed daily history supplies a labeled trend proxy.

Reference trades older than 90 seconds or in the future are unavailable. Histories
must have 50–130 ordered, complete sessions, valid OHLCV, the requested symbol and a
last observation no older than seven calendar days. Current New York date bars are
excluded conservatively, including after the close. These are daily metrics, not
intraday breakout confirmation. Unadjusted splits can distort the results.

The dedicated, identity-checked GET-only news adapter requests at most ten Alpaca
news items. Only Benzinga HTTPS headlines with explicit symbol attribution and
publication/update times within the captured 72-hour window survive normalization.
No full article content is stored. Earnings/financing/clinical/merger/split tags are
literal headline topics, not sentiment, proof of truth, or proof of price causation.
Unavailable entitlements, credentials, symbols, timestamps and providers produce
explicit missing evidence; original-account news fallback does not exist.

The existing daily grade remains a separate heuristic. Context has no invented
probability, trained AI model or trading authority. A high grade predicts no return.
GET `/auto-trader/pro-ticker/manual-signals/{id}/research-context` authenticates the
owner and recomputes the saved report, rejecting evidence/report disagreement.
Historical freshness means fresh at observation, never fresh now.

Provider references: [Alpaca news REST](https://docs.alpaca.markets/us/v1.4.2/reference/news-3)
and [news schema](https://docs.alpaca.markets/us/docs/streaming-real-time-news).

## Execution analytics

GET `/auto-trader/pro-ticker/performance-analytics` reads a consistent SQLite
snapshot. Only complete terminal linked-order coverage, matching individual priced
executions and matching posted share totals produce a closed outcome. Partial/open
or incomplete synced intents are counted separately. Full trade results are
filtered by final execution time using optional timezone-aware `start` (inclusive)
and `end` (exclusive) parameters. Days use America/New_York. Empty results are
unavailable, not fabricated zero performance.

The report includes Decimal gross results, daily/cumulative realized totals,
daily realized-curve drawdown and comparisons by frozen grade, research version,
market regime and strategy version. Missing strategy versions are explicitly
unattributed; grading version is never relabeled as strategy version. Entry
execution averages can be compared with the saved allocation reference. Exit
averages and planned protection levels are evidence, not an optimal-exit claim.

Fees, adjustments and net results remain unavailable because verified cost
attribution is not supported by the current FILL-only journal. No assumed zero
fees, fee estimates or returned virtual allocations enter profit calculations.

An explicit same-origin, owner-authenticated POST to
`/auto-trader/pro-ticker/performance-analytics/capture` accepts only `{}`. It reads
the configured dedicated PAPER account, checks every broker holding against the
owned strategy ledger, reconstructs weighted-average cost from attributed priced
executions and captures fresh marks. A ledger fingerprint recheck prevents a
concurrent accounting change from producing a saved mixed-state valuation. Market
reads occur outside the database read transaction. No orders or releases occur.

Observation history is stored in one new additive table,
`pro_ticker_equity_observations`, created only by the explicit capture operation.
The history rejects a different account fingerprint. Raw broker equity/cash and
owned gross unrealized marks are labeled separately from closed gross P/L. Cash
flows/corporate actions are unverified: raw observed equity drawdown is not an
adjusted strategy return, an inception series, or maximum intraday drawdown.
Historical points are never manufactured before observation began.

## Strategy evaluation

The optional timezone-aware `split_at` defines separate retrospective development
and holdout cohorts. Trades opened before and closed at/after the split are
excluded. Missing entry times are excluded. Saved entry metadata is used; no later
grade enters an earlier trade. Without a split, evaluation is unavailable.

Each cohort exposes group sample sizes and gross results. Groups with at least
20 closed observations can receive an explicit review recommendation; this
threshold is a display guard, not statistical significance. Recommendations never
change settings, risk or strategy behavior. A split chosen retrospectively is
not prospective validation. Existing 24+ hour forward quote research stays separate
from execution P/L. False-breakout paths, spread impact and optimal-exit analysis
remain unavailable without verified intraday/bid-ask evidence. No model training,
parameter search, predictive accuracy or automatic strategy deployment occurs.

## Validation still required

- Actual hosted authenticated collection/readback and restart persistence for
  these new endpoints (the connector exposes names, not owner credentials).
- A representative production backup on a disposable copy. The reported Windows
  backup is not present in this workspace; it has not been accessed or modified.
- Real provider entitlements/data coverage, verified fees/adjustments, cash flows
  and intraday paths where currently unavailable.
- Actual supervised PAPER order certification, deferred while trading is paused.
- Explicit owner approval before merge or production deployment.

## Production deployment and rollback

1. Keep trading paused and PR #1 draft. Verify exact final-commit CI and isolated
   staging before approving any production operation.
2. Make a fresh online SQLite backup, preserve the October 26 verified backup,
   verify the downloaded copy independently and compare cryptographic checksums.
3. Run the existing disposable-copy migration validator against representative
   production data. Check original values, links, reservations and foreign keys.
   Do not run a destructive migration or validation on the source backup.
4. Record the current production deployment/commit/configuration and retain its
   deployable image. Review flags; never enable live trading or unattended buys.
5. After explicit approval, deploy the reviewed commit. Check authentication,
   original-trader views, dedicated account isolation, health, research and reports.
   No broker order is required for these checks.
6. On failure, pause new activity and restore the previous application image first.
   Leave additive tables and immutable new evidence in place. Do not overwrite
   a live database with an older backup or run down-migrations automatically.
   A database restore requires separate approval and reconciliation of subsequent
   records; preserve the failed-state database for investigation.
