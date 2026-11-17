# Stock learning recovery and prospective collection

This update addresses drift between complete PAPER broker fills and the internal
trade/learning journal, plus research collectors that required manual clicks or
spent limited feature lookups on stocks outside the saved filters.

## Accounting

A five-minute worker first requests original nested PAPER orders for up to five
stale internal entries. Only verified original bracket ownership and nonconflicting
fill amounts can refresh the saved ledger; the evidence and prior ledger records
are audited atomically. It then reads the canonical broker ledger and repairs or
imports only fully closed automatic entries whose sell allocations have explicit
broker bracket or saved order links. It requires a complete ledger, valid
chronology, conserved quantities, consistent calculated P/L, unique entry IDs
and exclusive sell-order ownership. FIFO-only, partial, incomplete, conflicting,
and shared-exit allocations remain unresolved and are counted in Research Lab.
A weighted exit price represents multiple fills; no synthetic sell-order link is
created. Each update, learning outcome and before/after audit commit atomically.
Unknown historical entry features remain unknown. Existing entry metadata is
preserved. Large price moves are flagged for raw-fill/corporate-action review,
not silently discarded or declared erroneous.

Recovered closes from before installation are excluded from the 25-new-close
review counter. Historical reports can expand without retroactively triggering
settings changes. Successful future recoveries count once. The legacy exit
logger also requires an exact linked full-size sell before closing an entry;
partial and symbol-only matches wait for canonical reconstruction.

## Research

The paired-filter worker requests current quotes for quality-eligible candidates
before selecting up to three costly float/intraday lookups. A stock is skipped
only if neither frozen arm can pass its basic filters. The full original checks,
quote refresh, revision check and promotion requirements still apply.

A separate GET-only collector rotates through fresh stocks in the saved price
range, independently of the daily BUY score/rank gate. It freezes at most one
intraday experiment every five minutes during its regular-session window. The
existing exchange calendar and completed-bar strategy checks determine whether
evidence is usable; no missing data are filled in. It evaluates at most two due
minute paths per cycle, retrying different experiments fairly. Minute-path
retries are limited to seven days; older holes remain unmeasured in the lab.

New manual Pro Ticker grades receive descriptive follow-up prices after 24–48
hours, retaining actual elapsed time. Older missed horizons are reported and
are not backfilled with today's price. Up to two are evaluated per cycle.
This does not configure the separate dedicated Pro Ticker gateway, enable plan
release, train a model, promote an intraday strategy or send any order.

Research Lab displays the latest checks, cumulative audited updates, blocked
accounting categories, large-move flags, missing manual horizons and collector
errors. Trading settings and existing protective orders are unchanged.

## Verification

Offline regressions cover weighted multi-exit recovery, actual canonical ledger
round trips, historical learning exclusions, future-close idempotency, ambiguous
and truncated inputs, transaction rollback, quote-first research selection,
GET-only background collection, due evaluation, missed horizons, five-minute
sampling and fair retry selection. Existing stock, accounting, controls,
intraday strategy, manual-grade outcome and frontend tests are also exercised.
