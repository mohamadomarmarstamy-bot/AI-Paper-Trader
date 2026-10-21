# Pro Ticker completion and validation checklist

## Implemented in the dedicated workspace

- [x] Reuse the existing Pro Ticker navigation entry and section; eight accessible tabs.
- [x] Read strategy-scoped reservations, attributed positions, fill events and saved research.
- [x] Consistent SQLite snapshot; complete budget aggregate despite limited displayed records.
- [x] Exclude original trader account equity and trade-book P/L from Pro Ticker views.
- [x] Explicit unknown performance, broker connectivity and protection states.
- [x] Research search, direction filter, score/symbol sorting and symbol deduplication.
- [x] Inspect saved evidence; distinguish historical research from current AI evaluation.
- [x] Hypothetical proposal calculator using operator reference prices and current virtual allocation limits.
- [x] Research-to-proposal symbol handoff clears stale prices, share quantities and protection assumptions; historical source prices are never inferred as current quotes.
- [x] Proposal requires owner session, same-origin JSON; never reserves budget or submits orders.
- [x] Journal symbol/date filters and CSV export of posted share events.
- [x] Reuse manual research intake and manual release approval/history controls.
- [x] Scoped responsive styling; keyboard tab navigation and refresh/error states.
- [x] Operator-triggered public article discovery, persisted run history/status, single-run lease and interrupted retry protection.
- [x] Skip saved URLs, bound article collection, deduplicate candidate symbols and retain partial successes.
- [x] Public-source redirect allowlist; no email or broker calls from workspace discovery.
- [x] Exclude symbol-less article records from candidates while reporting their count; financial views remain available.

## Highest-priority remaining integration

- [ ] Dedicated discovery job with persisted run status, errors, fresh quote/indicator snapshots and candidate diversity.
- [ ] AI research evaluation with provenance, bullish/bearish evidence, regime and supported news sources.
- [ ] Persist verified execution prices, actual execution timestamps and reservation attribution in the fill journal.
- [ ] Compute realized P/L, unrealized marks, equity series, drawdown and cost-adjusted metrics only from verified priced data.
- [ ] Wire proposals to atomic reservations, independently checked broker admission and authorized PAPER submission.
- [ ] Persist execution intent before network calls, resolve interrupted submissions by client order ID, and verify protective orders.
- [ ] Connect entry/partial exit/complete exit reconciliation and release review end-to-end through HTTP/UI.
- [ ] Keep automatic release disabled unless separately approved.
- [x] Versioned Pro Ticker proposal allocation/estimated-stop-risk limits and manual discovery size, with authenticated same-origin saves, optimistic concurrency and atomic audit records.
- [ ] Execution/scanner strategy settings beyond hypothetical proposals and bounded manual discovery.
- [ ] Link journal outcomes and forward research to measured strategy/regime comparisons.

## Validation and external blockers

- [ ] Complete workflow must pass end-to-end tests; module coverage alone is insufficient.
- [ ] Validate a representative standalone exported database on a disposable copy. No export has been supplied.
- [ ] Verify a saved record through an actual hosted restart. Owner login value is redacted to the connector; do not reset it or expose it to obtain test access.
- [ ] Dedicated Alpaca PAPER account validation. The owner currently has one actively used PAPER account; do not reuse it for staging tests.
- [ ] New staging deployment requires explicit approval. Existing hosted staging remains pinned to its previously approved build; pushing the feature branch is not deployment approval.
- [ ] Full PR review and explicit merge approval. Keep PR #1 draft.

## Interpretation

Public discovery is an explicit operator action, not an automatic market scan.
It reads one public listing page, inspects at most five article links through
the existing signal collector, and collects at most five previously unseen
signal articles per run. The legacy collector refetches selected articles for
storage. The collection loop checks elapsed time before additional work;
listing inspection, redirects and in-flight HTTP calls can extend beyond its
120-second window. Empty results can mean that the inspected articles do not
contain ticker/signal evidence; general market news is not promoted to a signal.
A ten-minute database lease prevents concurrent runs. A lost browser response
does not imply server failure: refresh persisted status before retrying.
Expired runs remain visible as interrupted; only an explicit retry starts new
work. Empty source responses are not reported as proof of scanner health.

The workspace shows a configured virtual allocation, not a dedicated brokerage
account. Share fill events currently have no execution price, so performance is
unavailable. Candidate rows are historical saved research, not an operational
independent scanner. Proposal calculations use operator estimates, are not
broker-approved plans, and do not claim a stop will cap actual losses. No chart
or measured strategy improvement is fabricated to fill missing integrations.

## Pro Ticker proposal/research settings

Settings are persisted separately from the original auto-trader settings. The
maximum hypothetical allocation and estimated stop-risk limits are in integer
cents; the discovery article limit is 1–5. Initial financial ceilings equal the
existing configured virtual budget to preserve prior calculator behavior; they
are not strategy recommendations. The owner can lower them from Settings &
Health. Proposals report the saved revision and separate virtual-budget and
proposal-risk checks. Passing either check never authorizes execution.

Each save requires the current revision. A conflicting save returns HTTP 409
without modifying settings or audit history. Interrupted audit writes roll back
the settings change. After an uncertain response, reload the saved state before
retrying. Browser refresh preserves unsaved edits; explicit reload discards them.
Discovery captures its settings revision when it starts; changing settings does
not alter a run in progress. These fields cannot enable live/PAPER execution,
automatic discovery, or automatic budget release.

## Manual Signals workflow

The dedicated Manual Signals tab accepts one stock and a stable request UUID.
It stores an immutable submission-time reference quote and a provisional grade
from the existing daily technical analyzer, including its version, raw -6..+6
score, heuristic confidence, indicators and reasons. Grades A/B/C/D are not a
trained model or calibrated win probabilities. Missing, malformed, stale and
future market data are distinguished; missing analysis never produces a grade.
Indicator as-of time is unknown, so these rows are not training-ready.

A duplicate request returns the original review without refreshing its score or
reference price. A different request for a stock already being graded is blocked.
Expired workers cannot overwrite finalized reviews. No browser-provided grade,
quote, outcome, execution flag or identity is accepted. Broker order submission
is never called by this workflow.

Still required for the user's enter -> PAPER buy -> learn target: explicit
per-entry sizing/risk admission, durable order intent and client-order-ID retry
resolution, verified PAPER broker fills and protection, outcome attachment to
those exact fills, loss-inclusive point-in-time learning data, and independently
validated model evaluation. The manual grade does not enable automatic buying,
change model parameters or claim to detect original Pro Ticker alerts.

## Separate forward research for manual grades

An operator may request a fresh quote check at least 24 elapsed hours after the
stored grade completed. This is not a trading-day close or realized PAPER trade.
The first verified fresh forward quote is stored separately; retries return the
same result even if a later quote is more favorable. Original entry grades and
reference snapshots remain unchanged. Stale, missing or future reference/forward
quotes cannot produce an outcome.

The Performance tab groups positive, negative and flat raw quote changes by grade
and grading version, reports the evaluated denominator, and labels any limited
500-review window. These returns exclude costs, slippage and corporate actions;
manual selection of which reviews to evaluate can bias them. The reference quote
precedes daily analysis and indicator as-of times are unknown. Training-ready
samples therefore remain zero: this descriptive research neither updates an AI
model nor changes automatic trading thresholds. Verified execution outcomes,
point-in-time feature validation, loss-inclusive datasets and chronological
out-of-sample evaluation are still required for adaptive signal detection.

## Saved manual-signal PAPER drafts

Manual Signals now offers an explicit share/stop/target form on saved BUY grades.
The owner can save one immutable research draft per review using a new server
quote (90-second freshness limit). The draft preserves the grade, server quote,
settings revision, sizing and estimated risk. It survives application recreation
and is shown when inspecting the saved review. Request IDs bind the exact intent;
unchanged retries return the first draft and conflicting intent is rejected.
Unique database constraints and an atomic insert handle concurrent retries and
interrupted writes. Provider failures and stale quotes leave no partial draft.

Drafts reserve **no funds**, submit **no orders**, and grant **no execution
permission**. Their captured budget/risk checks are informational as of creation;
all admission evidence must be independently revalidated before a future order.
Broker account identity, market session, positions/open orders, protective orders
and actual reservations remain unresolved admission blockers. This is progress
on the manual signal workflow, not completion of automatic buying or learning.

Validation includes signed-owner HTTP authentication, cross-origin rejection,
provider error sanitization, concurrency, rollback/retry, stale quotes, duplicate
intent, risk/budget rejection and persistence across staging app recreation.
Chromium tests exercise desktop/mobile form submission and a lost confirmation
followed by an unchanged retry, using synthetic fixtures only. No hosted restart
or deployment is performed by these tests.

### Sizing boundary hardening

Proposal costs and stop-risk estimates round **up** to cents, so fractional-cent
exposure cannot pass a cent-denominated limit by rounding down. Stop and target
levels that collapse to zero or the entry price at supported precision are
rejected. Pure entry plans reject malformed/non-ASCII symbols and extreme integer
inputs without raising overflow errors. Draft saves reject an explicitly
mismatched provider symbol and recheck quote freshness after acquiring the write
lock. These remain review-only estimates, not executable price guarantees.

Risk sizing also uses the larger of the requested stop distance and the distance
to the rounded stop price. This avoids understating losses when four-decimal
rounding widens the stop on a large share count. Saved draft details show stop,
target and save time, while continuing to require fresh admission before orders.

## Explicit draft allocation intents

Saved BUY drafts now support an owner-confirmed virtual allocation. A new server
quote is checked under a SQLite write transaction against current settings,
remaining strategy budget and the original draft's maximum cost/risk. Any price
increase beyond those saved ceilings is rejected. The reservation, immutable
planned shares, stable client order ID, intent and owner audit event commit
atomically. Concurrent requests and retries cannot allocate the same draft twice.
The original research draft stays immutable and is not reclassified as a fill.

Manual Signals provides a separate unchecked approval box for allocating funds.
The PAPER Workspace lists persisted allocation intents. Neither action enables
broker submission, verifies broker buying power, or authorizes automatic trading.
There is no submit-order endpoint for these intents.

An owner can explicitly cancel an allocation only while its intent is prepared,
no submission attempt or broker link exists, reservation identity/amount/shares
still match, and no open strategy position or new fill evidence contradicts the
never-submitted state. Cancellation, returned virtual funds and the audit event
are atomic and retry-safe. Submitted/uncertain intents remain blocked for broker
reconciliation. Returned funds are never recorded as trading profit. Automatic
budget release remains disabled.

Tests cover transaction rollback after an audit failure, concurrent allocation
and cancellation, competing-symbol budget exhaustion, intent/request conflicts,
settings changes, stale/future/mismatched quotes, original approval ceilings,
explicit owner confirmation and sanitized provider errors. Signed-owner staging
app recreation verifies intent/reservation persistence without broker calls.
Browser fixtures exercise lost allocation confirmation, unchanged retry, explicit
cancellation and desktop/mobile history. Hosted restart/account validation still
requires the separately documented access and approval.

## Research evidence export and training readiness

Performance now includes authenticated JSON/CSV research evidence export and a
readiness summary. A consistent database snapshot contains the latest 500 manual
reviews, preserving graded, unavailable and unevaluated rows, as well as gains,
losses and flat forward quotes. The full count and limited export window are
explicit; no outcome-based filter selects only successful examples.

Entry features are whitelisted into a separate object from later outcomes.
Original reference timestamps and versioned grades remain unchanged. Every row
includes explicit training exclusions: unverified indicator as-of times, reference
preceding analysis completion, absent verified execution outcomes, unmodeled
costs/corporate actions, manual sample-selection bias and missing out-of-sample
evaluation. Training-ready samples stay zero; exports do not train or alter any
model. CSV textual formula injection is escaped while negative numeric returns
remain numeric. Authentication, no-store headers, bounded snapshots, CSV download
and desktop/mobile readiness behavior are tested with synthetic fixtures.

### Next implementation priorities after these checkpoints

1. Broker admission/uncertain submission recovery using the reservation-specific
   client order ID, isolated from the original trader. Actual order submission
   stays disabled until a dedicated PAPER account and tested protection exist.
2. Priced verified executions and reservation-linked closed-trade outcomes;
   measured journal/performance UI must not use quote research as trading P/L.
3. Full simulated order -> partial fills -> protection -> exit -> reconciliation
   -> owner release -> priced journal integration through authenticated HTTP/UI.
4. Independent fresh scanner jobs and chronologically evaluated signal research.
5. Hosted persistence and representative database-export validation. New hosted
   deployments require explicit approval; production PAPER credentials cannot be
   reused for staging.

## Allocation-to-accounting integration checkpoint

Intent reads now reconcile their displayed state against the actual reservation
ledger in a consistent read transaction. Verified broker links appear as linked,
and released linked reservations appear closed only with an existing owner
release event for that allocation. Mismatched reservation identity, quantity,
client ID or unsupported release evidence fails closed. Broker-linked/closed
intents never display the never-submitted cancellation action. Original draft
and intent plans stay immutable; no broker submission service is enabled.

A combined integration test drives authenticated HTTP manual grading, draft
creation, allocation, workspace/history and explicit release approval. A trusted
synthetic broker fixture separately exercises the existing entry/exit ownership
links, partial fills, duplicate/stale protection, open-position release rejection,
completed exits and exactly-once owner-approved release. It verifies the journal,
virtual balance, unchanged entry grade and zero training-ready samples afterward.
Browser fixtures cover linked/closed intent rendering on desktop/mobile.

This is NOT a full execution end-to-end test: broker submission, protective order
placement, scanner automation, actual fills/prices and live hosted persistence
are not exercised or implemented by this checkpoint. Quote research remains
separate from unavailable realized trading profit.

Allocation inspection also exposes the persisted owner audit events and their
server-recorded times. Read-only status changes do not fabricate new owner
actions; the existing Budget release history remains the audit for a linked
reservation's approved closure.
