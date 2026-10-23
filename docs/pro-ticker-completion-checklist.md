# Pro Ticker completion and validation checklist

Current checkpoint: October 23, 2026. The checklists through **Validation and
external blockers** describe current completion. Later implementation notes are
historical milestones; references there to missing broker adapters, execution
prices, independent discovery or dedicated account setup have been superseded.
See [release readiness](pro-ticker-release-readiness.md) for deployment gates.

## Implemented in the dedicated workspace

- [x] Reuse the existing Pro Ticker navigation entry and section; nine accessible tabs.
- [x] Read strategy-scoped reservations, attributed positions, fill events and saved research.
- [x] Consistent SQLite snapshot; complete budget aggregate despite limited displayed records.
- [x] Exclude original trader account equity and trade-book P/L from Pro Ticker views.
- [x] Explicit unknown net/open-position performance, broker connectivity and protection states; separately reported owner-reconciled gross closed estimates where prices exist.
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

- [x] Operator-triggered independent mover discovery, rotating bounded batches, persisted status, timestamps and explicit failures. Automatic scanning remains disabled.
- [ ] AI research evaluation with provenance, bullish/bearish evidence, regime and supported news sources.
- [x] Persist individually verified execution prices, broker timestamps and reservation attribution; complete streamed execution CSV export. Actual broker activity coverage remains uncertified.
- [ ] Compute realized P/L, unrealized marks, equity series, drawdown and cost-adjusted metrics only from verified priced data.
- [x] Wire saved BUY drafts to explicit owner-approved atomic reservations and durable client-order-ID intents.
- [x] Independently checked broker admission and explicitly owner-approved PAPER submission are implemented and tested with mocked broker evidence. Hosted submission remains disabled.
- [x] Persist immutable planned shares, reservation-specific client order ID, allocation intent and owner audit together.
- [x] Exact-client-ID submission recovery and protective-order inspection are implemented. Real broker certification is still required.
- [x] Combined authenticated HTTP/accounting fixture covers manual grading -> draft -> allocation -> verified partial fills -> partial/canceled exits -> owner release -> gross reporting.
- [x] Connected authenticated HTTP and desktop/mobile UI fixtures cover owner dispatch, fills, protective-order observation, management, reconciliation and manual release. Broker responses are mocked.
- [x] Keep automatic release disabled unless separately approved.
- [x] Versioned Pro Ticker proposal allocation/estimated-stop-risk limits and manual discovery size, with authenticated same-origin saves, optimistic concurrency and atomic audit records.
- [ ] Execution/scanner strategy settings beyond hypothetical proposals and bounded manual discovery.
- [ ] Link journal outcomes and forward research to measured strategy/regime comparisons.

## Validation and external blockers

- [x] Complete mocked workflow passes HTTP/UI integration checks; module coverage alone is not the evidence for this item.
- [ ] Actual dedicated-account bracket entry, protection, cancellation, close, recovery and execution activity certification. A BUY research grade and fresh quote are required; a rejected candidate is not a completed order test.
- [ ] Validate a representative standalone exported database on a disposable copy. No export has been supplied.
- [x] Verify a saved research record through an actual hosted restart. Signed-owner save and independent readback retained the original timestamp, grade and score. Broker lifecycle persistence is a separate certification.
- [x] Separate dedicated PAPER account configured; authenticated hosted read-only verification checked expected account identity and active, unblocked USD readiness. Original account credentials are not reused.
- [x] Actual isolated-staging database snapshot passed disposable-copy integrity, original-value, foreign-key and reservation checks. This research-only sample does not satisfy representative production-data validation above.
- [x] Isolated staging deployment and subsequent staging updates/restarts are authorized by the completion sprint. Production deployment and merge still require separate approval.
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

The workspace shows a configured virtual allocation, not broker account equity.
The dedicated PAPER account is configured and read-only identity checks passed.
Individually verified fill activities can retain execution prices and timestamps;
actual broker activity coverage remains uncertified. The closed report captures
gross estimates separately from unknown net/open-position P&L and equity-level
performance. Independent discovery is operator-triggered; saved research remains
historical evidence. Proposal calculations use operator estimates and cannot
guarantee that a stop caps actual losses.

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

Explicit per-entry sizing, owner-approved virtual allocation and durable order
intent now exist. Still required for the user's enter -> PAPER buy -> learn
target: independent broker admission, client-order-ID submission recovery,
verified execution/protection, exact execution outcome attachment, point-in-time
learning data and independently validated model evaluation. The manual grade does not enable automatic buying,
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

## Closed PAPER gross-outcome reporting

An owner-approved, independently re-fetched terminal closure now captures a
separate immutable performance record in the same transaction as the release.
Valid broker `filled_avg_price` and filled quantities produce a gross closed
estimate from entry cost and all linked exit proceeds. Missing or invalid prices
produce an unavailable record rather than zero profit; they do not substitute
for share-closure verification. A failed performance write rolls back the release
and audit together. Exact retries do not fetch a later price or rewrite results.

The authenticated closed-performance report verifies each record's release-review
and evidence-digest linkage. It includes gross gains, losses, flat outcomes,
win rate, profit factor and average gain/loss for the priced closed subset.
Aggregate totals cover all recorded priced outcomes even if displayed rows are
limited. Legacy releases and unavailable-price records remain counted as
unpriced. Canceled, never-submitted allocations are not counted as closed trades.

Performance displays this separately from forward-quote research, including
coverage, positive/negative gross estimates, and server record times explicitly
labeled as not execution times. Broker average prices may themselves be rounded;
exact execution/lot prices, fees, net P/L, open-position valuation, dated equity
and drawdown remain unavailable. No equity curve or daily realized result is
fabricated, and model training stays disabled.

The combined simulated HTTP/accounting workflow now also captures a $2 gross
closed estimate from a fabricated 4-share $10.50 entry and $11 exit, while returning
its separate $42 virtual reservation once. This is a synthetic test result, not
an account performance claim. Tests also cover losses, missing/invalid prices,
report linkage, atomic rollback, immutable retries, multi-trade summary coverage,
authentication and desktop/mobile rendering with invalid-data rejection.

The integrated workflow also covers a canceled partial exit followed by a second
linked exit: both exit orders' filled quantities and average prices contribute
to closure and the negative gross estimate. A loss remains a loss after budget
return, and retrying closure still creates only one performance record.

Forward-research evaluation additionally rejects provider snapshots naming a
different symbol and rechecks quote freshness after acquiring the outcome write
lock. Expired/mismatched evidence cannot become an immutable learning label;
unchanged retries can recover after a rejected attempt. Research summary reads
use one database snapshot for counts, grades and outcomes.

## Dedicated PAPER admission evidence and uncertain-attempt recovery

The workspace now exposes server-authenticated admission/recovery status. Normal
main and isolated staging do not supply an execution context: both controls are
disabled, no credentials are loaded, and no fallback to the original trader's
PAPER credentials exists. There remains no submit-order endpoint.

An explicitly constructed server context requires a separately approved dedicated
account UUID and a PAPER-only gateway. The new adapter supports GET only, pins
trading reads to `https://paper-api.alpaca.markets`, pins quote reads to Alpaca's
market-data host, refuses redirects and unexpected/malformed HTTP statuses, and
uses bounded timeouts. HTTP/credential failures are sanitized. It contains no
submit/cancel/replace method and reads no environment credentials.

Read-only admission reviews preserve a request ID, 30-second expiry and ledger
fingerprint. They check account identity/status/currency/block flags, buying
power, fresh regular-market clock, tradable equity, complete position/open-order
collision snapshots, fresh trade data, reserved entry ceiling, unchanged ledger
and current risk settings. Nested orders and a potentially truncated 500-order
snapshot cannot imply no collisions. Passing every check still does not authorize
execution: broker submission stays disabled and protection remains unverified.

Recovery requires an already recorded submission attempt. It looks up the exact
reservation-specific client order ID on the dedicated account, verifies the
broker UUID/client ID/symbol/buy side/bound quantity/fills, and atomically links the
existing order and recovery audit. Cumulative entry-fill posting is retry-safe,
including an interruption after linking and out-of-order snapshots. A not-found
lookup is not proof that no submission occurred: funds stay reserved, cancellation
remains blocked, and the service never sends another buy. A zero-fill rejection
also cannot trigger automatic budget release here. Recovery does not claim that
protective orders exist.

The UI distinguishes uncertain submission from no order, exposes read-only checks
and existing-order lookup only when server-configured, and never enables the
existing disabled submit control. Tests use separately constructed synthetic
PAPER gateways; actual account access and deployed admission/recovery remain
blocked pending approval/configuration and dedicated-account validation.

Implementation references: Alpaca [order tracking](https://docs.alpaca.markets/us/docs/working-with-orders),
[order lifecycle and buying power](https://docs.alpaca.markets/us/docs/orders-at-alpaca),
and [PAPER account retrieval](https://docs.alpaca.markets/us/reference/getaccount-1).

### Existing bracket protection inspection (in development)

A default-off owner endpoint and UI control inspect existing PAPER bracket exits
without submitting, replacing, canceling or linking an order. The service verifies
the owned parent, independently fetched children, complete entry accounting,
matching broker position, fresh regular-session clock/trade, active stop/target
quantities and the original cost/risk limits. Partial entries/exits and after-hours
coverage deliberately fail certification. Saved observations expire after 30
seconds and are invalidated by ledger changes. No observation guarantees a stop
execution price, authorizes another order, or releases funds. The normal app and
staging factory still have no dedicated gateway configured.

This does not complete actual order submission, autonomous scanning, validated
model training, execution-level net analytics, or hosted persistence validation.
Browser checks use synthetic responses; dedicated account validation remains open.

## Owner-confirmed PAPER dispatch checkpoint

A separately composed `PaperSubmissionContext` can enable an authenticated,
same-origin owner-confirmed submission for a saved, reserved intent. Standard
main/staging composition still provides no execution context or credentials.
The gateway accepts only whole-share long limit GTC brackets on the fixed Alpaca
PAPER host, checks the dedicated account, sends no redirects and never retries
POST. The buy limit cannot exceed the reserved cost. Stops and targets round up
to broker precision without widening approved estimated risk. Regular-session
admission is freshly repeated before claiming the intent.

The dispatch claim, timestamp and owner audit commit atomically before transport.
Duplicate/concurrent calls, a crashed dispatcher, missing lookup or invalid
response cannot send a second buy. Recovery uses the exact persisted client ID,
independent account identity and order ownership/fill checks. Funds stay held
when uncertain or zero-fill rejected; automatic release remains disabled.
Partial bracket entry fills are explicitly not certified as protected. Existing
protection inspection is required after full entry. Broker-native exits are
requested, but application exit monitoring/reconciliation integration remains
unfinished. No dedicated broker account has been tested.

Synthetic HTTP/service tests exercise authorization, admission, dispatch,
partial entry fills, restart-style recovery and failure boundaries. Browser
fixtures exercise the explicit confirmation control, separate from the default
configuration that cannot submit. These do not establish real broker readiness.

## Integrated market research scanning

The Scanner tab can scan eight entered symbols or rotate through saved research
and manual-review symbols. Empty universes stay empty. The existing keyless Yahoo
Finance dependency supplies timestamped minute closes and current regular-session
volume, with 20 completed daily sessions as the average-volume denominator.
Fractional average volume is preserved. This ratio is not time-adjusted RVOL.
Data over 120 seconds old, future data, missing history and provider failures are
rejected explicitly. Movement is measured against the last completed session;
missing movement and research scores stay unavailable. Results rank relative
volume, not AI confidence. A candidate can open Manual Signals for a separate
technical grade, without creating an order.

Owner authentication, same-origin bounded JSON, a persisted single-run lease,
immutable request retries, provider error sanitization and persisted run/history
are wired through the actual app/staging router. Interrupted workers cannot
publish after lease expiry. Scanning is operator-triggered; independent universe
expansion and background scheduling are still not implemented. Yahoo live-source
coverage, hosted persistence and staging remain unverified while Railway OAuth
is unavailable. No broker credentials are used by this scanner.

Browser CI uses the version-matched Playwright container with preinstalled
browsers, avoiding repeated distro dependency installation timeouts.

## Bracket reconciliation and dedicated runtime composition

An owner-triggered monitor now refreshes the exact saved entry and independently
fetches the owned parent and both bracket children. It atomically records exit
ownership/provenance and uses the existing cumulative fill bridge to post exits
idempotently. Broker OCO quantity reductions are accepted only for an unfilled,
previously verified child; every change is audited. Foreign attribution, stale
exit fills, changed filled quantities, inconsistent children, and double-filled
OCO quantities fail closed. Both children remain linked, including zero-fill
siblings, so an open sibling blocks closure/release review. Monitoring never
places/cancels orders or releases budget. Background scheduling and safe manual
bracket cancellation/closing remain unfinished.

A synthetic authenticated HTTP integration covers grade -> draft -> reserve ->
explicit owner submission -> partial/full entry -> protection observation ->
partial/full exit -> closure -> manual release approval -> journal/gross report.
This is application integration with a mocked broker, not live-account validation.

Runtime accepts only separately named dedicated-account configuration:
`PRO_TICKER_PAPER_ACCOUNT_ID`, `PRO_TICKER_ORIGINAL_ACCOUNT_ID`,
`PRO_TICKER_PAPER_KEY_ID`, `PRO_TICKER_PAPER_SECRET_KEY`, and exact
`PRO_TICKER_DEDICATED_ACCOUNT_CONFIRMED=true`. Account IDs must differ; the key
must differ from any original trader key in the process. Missing/invalid config
keeps the gateway off. Owner dispatch additionally requires exact
`PRO_TICKER_OWNER_PAPER_SUBMISSION_ENABLED=true`. No setting is enabled by this
commit. Automatic trading/release remain disabled. Keys belong in deployment
variables, never request bodies or git.

Both main and staging compose this dedicated context. Pro Ticker's manual release
reader also uses this account, never the original trader credential fallback.
Staging refuses original trader broker credential names entirely; its health
response reports the actual owner-submission gate. Original trader execution
configuration is unchanged. A separately provisioned PAPER account and hosted
validation remain required before enabling any real broker calls in staging.

## Individual broker execution journal

An explicit owner read-only sync retrieves bounded, order-filtered Alpaca FILL
activity pages from the dedicated account. Every activity must match a linked
order's symbol, side, broker UUID and independently checked filled quantity.
Execution IDs, per-fill prices, quantities and broker execution times are stored
immutably in one transaction, separate from the cumulative share-posting ledger.
Activity gaps, duplicates, future/naive timestamps, changed activities, stale
coverage, wrong ownership or share-ledger disagreements fail closed. Unchanged
retries do not duplicate records. No order or budget release occurs.

Trade Journal now shows these individual execution prices/times, a symbol filter,
a limited-window CSV export and coverage counts. Closed gross results are computed
with Decimal only for fully covered owner-reconciled reservations; returned virtual
funds are never income. Net P&L/fees/corporate actions/trade corrections remain
unverified, not zero. Existing terminal-average estimates and research reports
remain clearly separate. This endpoint does not train an AI model.

The synthetic HTTP lifecycle now includes execution-history sync and journal/gross
report refresh after explicit release. Real account activities and data-provider
coverage still require dedicated-account validation. Implementation references:
[Alpaca FILL activities](https://docs.alpaca.markets/us/docs/account-activities) and
[order-filtered activity pagination](https://docs.alpaca.markets/us/reference/getaccountactivitiesbyactivitytype-1).
