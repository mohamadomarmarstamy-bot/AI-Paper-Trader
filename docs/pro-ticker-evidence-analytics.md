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

Final net results and complete fee/adjustment totals remain unavailable without
broker evidence of final coverage. The separate cash ledger below now reports
known order-linked costs where supported. No assumed zero fees, fee estimates or
returned virtual allocations enter profit calculations.

An explicit same-origin, owner-authenticated POST to
`/auto-trader/pro-ticker/performance-analytics/capture` accepts only `{}`. It reads
the configured dedicated PAPER account, checks every broker holding against the
owned strategy ledger, reconstructs weighted-average cost from attributed priced
executions and captures fresh marks. Per-order/side priced executions must match every posted fill; matching net
shares alone cannot establish cost basis when an exit and re-entry are unsynced.
A ledger fingerprint recheck prevents a concurrent accounting change from
producing a saved mixed-state valuation. Evidence age is checked again after
acquiring the database write lock, immediately before saving. Market
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

## Verified validation and remaining requirements

- Hosted owner-authenticated research save, evidence replay, equity observation,
  date/cohort filtering and independent readback passed. After explicit user
  approval, only staging was restarted; saved research and equity survived.
- An actual staging snapshot passed disposable-copy integrity, foreign-key,
  original-value and reservation checks; its source checksum remained unchanged.
- The user ran the ebe9720 validator against the existing 602,501,120-byte Windows
  production-backup copy ending in ` (1).sqlite3`. The supplied output reported
  valid/integrity/foreign-key checks passing, existing rows/values and reservation
  accounting preserved, and source_modified=false. This is user-executed evidence;
  comparison with the Railway source checksum remains unverified. The original
  backup and its sidecar were left in place. The accounting-report fixes introduce
  no schema or migration change, so this validation remains applicable.
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

## Dedicated cash activity evidence

`GET /auto-trader/pro-ticker/cash-evidence` reports saved cash evidence. An explicit
owner-authenticated, same-origin `POST /cash-evidence/sync` under that Pro Ticker
base accepts only `{}` and reads the dedicated PAPER account. The gateway verifies
identity and fetches non-trade activity in at most five 100-row pages. A full last
page, duplicate IDs, wrong account/currency, changed records, disappeared saved
records or expired evidence fail closed. No account settings, orders, fills,
positions, budgets or release approvals are changed.

The additive `pro_ticker_cash_activities` and `pro_ticker_cash_syncs` tables retain
normalized immutable broker records and observation digests. Descriptions and
arbitrary provider/browser fields are not retained. Reported signs are preserved:
FEE/PTC/PTR/DIVFEE amounts are known costs/rebates, CSD/CSW/ACATC/TRANS are external
cash flows, and income/other adjustments are separately classified. Unsupported
crypto denomination cannot be represented as USD cost evidence.

An activity can affect a covered trade's **gross after known costs** only when its
explicit broker order UUID matches exactly one owned entry/exit and any supplied
symbol agrees. Symbol/date/amount similarity never creates attribution. Deposits,
withdrawals and unlinked cash adjustments never become trade profit. Account cash
history is labeled all-history, while trade-cost rows respect the trade report's
closing-time filters.

`gross_after_known_costs_usd` is partial cost evidence, not certified net P/L.
Empty history is not proof of zero fees. A complete fetched page window is not a
broker guarantee that later fees/corrections are final. Net results and final cost
coverage therefore remain explicitly unavailable. Changed existing activity IDs
require independent correction review rather than silently rewriting history.

References: [Alpaca account activities](https://docs.alpaca.markets/us/docs/account-activities)
and [Trading API activity retrieval](https://docs.alpaca.markets/us/v1.4.2/reference/getaccountactivities-2).

## Pre-registered intraday forward studies

A recent completed manual review can now register a fixed one-hour regular-session
study using `POST /auto-trader/pro-ticker/manual-signals/{id}/forward-study` with
only `{}`. The server freezes the saved prior 20-session high, research evidence
digest, grade/version, daily volume, market regime, two hypothesis versions and
future calendar window before the window begins. Input age is limited to five
minutes and checked again after the database write lock. A live trade reference
is not needed for this research-only plan when completed daily history is valid;
this exception does not affect draft/allocation/submission freshness rules.

The dedicated PAPER calendar accounts for holidays and early closes. The window
starts at the next full future minute inside a session with 60 minutes remaining,
or at the next known full session. No browser-supplied rules, dates, prices,
thresholds, bars or outcomes are accepted. The two fixed hypotheses require one
or two consecutive completed minute closes above the frozen prior high. The
reference entry is the following minute's open, never the confirmation close.
An entry must occur within the pre-registered window. The exit reference is the
last completed close in that window. These are hypothetical quote references,
not orders, verified fills or trade P/L.

Explicit evaluation after the window plus a one-minute publication allowance
uses raw single-symbol minute bars, with ticker remapping disabled. Every one of
60 timestamps must be present, ordered, unique and exactly aligned. A missing
minute, extra page, bad OHLCV, wrong symbol, early request, account mismatch,
expired worker claim or changed plan cannot produce a completed outcome. Provider
publication/subscription delays can require later retries; missing data is never
imputed. A durable lease prevents duplicate concurrent evaluations; retries replay
the original saved evidence/report instead of overwriting outcomes.

False-breakout research means a later minute close returned to/below the prior
high. Entry gap, holding duration, entry-minute volume, full-window volume and
favorable/adverse OHLC excursions provide descriptive entry/exit and liquidity
context. They do not establish executable stops, targets or optimal exits. When
available, the first provider quote within two seconds after the reference entry
supplies a separately labeled quoted spread. Subtracting one such spread is a
sensitivity scenario, not actual fees, slippage or net performance. Missing or
invalid quotes leave spread results unavailable without inventing zero friction.

`GET /auto-trader/pro-ticker/forward-studies` replays saved evidence and reports the
latest 200 plans, including unevaluated/error states and no-trigger windows.
No-trigger windows are not breakevens, losses or zero-return trades. Triggered
quote returns are compared by frozen hypothesis version, saved grade/version,
market regime, daily-liquidity band and observed spread band. At least 20 paired
triggered windows are required to name a historical review candidate; this is a
display guard, not statistical significance or predictive validation. Completed
plans with the same symbol, window, prior-high threshold and methods contribute
only once to comparisons; all plans remain visible and duplicates are counted.
Overlapping windows and correlated stocks are still not independent. Different
entry times imply different holding periods. Manual selection, corporate actions,
missing-feed coverage and costs remain limitations. Recommendations never apply
strategy changes, train a model or raise risk.

Manual Signals exposes registration/evaluation controls. Performance exposes
plan status, hypothesis/cohort comparisons and per-plan evaluation. All routes
require owner authentication; writes also require same-origin JSON and a
separately configured dedicated PAPER reader. The two additive study tables are
created only by explicit research actions. No broker write, virtual allocation,
original-trader change or background scheduler is added.

References: [PAPER market calendar](https://docs.alpaca.markets/us/v1.4.2/reference/getcalendar-1),
[raw historical bars](https://docs.alpaca.markets/us/reference/stockbarsingle-1), and
[historical quotes](https://docs.alpaca.markets/us/v1.4.2/reference/stockquotesingle-1).
