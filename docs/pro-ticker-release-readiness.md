# PAPER release preparation — approval is still blocked

## Current scope

- Dedicated workspace, owner-entered grading, persisted research and proposals.
- Rotating, operator-triggered market scans of saved/entered symbols using the
  existing keyless Yahoo dependency, plus independent bounded US equity mover
  discovery and saved daily-grade display. Stale/missing data is explicit.
- Dedicated-account owner-confirmed limit brackets, durable dispatch claims,
  at-most-once POST attempts, exact-client-ID recovery and reservation accounting.
- Owner-triggered bracket entry/exit reconciliation and protection observations.
- Opt-in unattended observation worker with durable claims, restart recovery,
  sanitized failure status, and authenticated monitoring status in the trading UI.
- Explicit manual release review/approval and idempotent budget return.
- Individual broker activity execution journal with gross closed coverage and CSV;
  net fees/adjustments and research estimates are not interchangeable.

Synthetic HTTP tests cover the connected lifecycle. Browser tests cover desktop
and mobile controls; synthetic fixture results are not actual account results.
Automatic stock buying, automatic budget release and model retraining are disabled.
Original trader execution settings and production deployment are unchanged.

## Blocking release gates

1. Inspect production deployment/logs, scanner, PAPER connectivity and actual order
   monitoring. Local full-app startup/regression checks do not prove hosted health.
   Railway access is restored. Both hosted health endpoints returned 200; a
   production BF-B research-symbol error was confirmed and fixed on the branch.
   No production deployment or actual entry/exit certification is claimed.
2. Separate Alpaca PAPER account setup is complete. The account identities differ,
   and authenticated hosted read-only verification checked the configured account's
   identity and active, unblocked USD readiness. Never reuse original credentials.
3. Exact-commit CI, isolated staging deployment, signed-owner UI review, independent
   discovery and read-only account verification passed. Health reports broker
   submission, position management and background monitoring disabled. This is
   connection/research validation, not an actual broker order lifecycle pass.
4. Hosted owner login, a saved research review, service restart and independent
   readback are now verified. The original timestamp, grade and technical score
   survived. This completes research-record restart proof, not broker integration.
5. Validate a representative exported database on a disposable copy. The original
   export must remain unchanged; no representative export has been supplied.
6. Validate bracket behavior, account activity pagination, partial fills and provider
   freshness against the dedicated account. Synthetic evidence is insufficient.
7. Unattended monitoring, owner cancel/close controls and bounded independent
   discovery are implemented. Dedicated-account certification and authenticated
   hosted broker workflow checks remain required. Bracket entries can be unprotected while partially filled;
   protection is not certified after hours. These must be addressed or explicitly
   accepted as restricted operator-supervised scope before production approval.
8. Net P&L, fees, corrections, corporate actions and broader risk-adjusted analytics
   remain unverified. Do not advertise research grades as AI win probabilities.

## Staging deployment checklist (authorized)

- Identify explicit staging project, environment and service IDs; inspect source,
  current deployment, volume and configuration without revealing variable values.
- Record the old deployment ID/commit and create a consistent backup before schema
  migrations. Retain the dedicated volume and owner authentication values.
- Deploy the exact tested feature-branch commit with the staging Dockerfile/factory.
  Do not switch the service to production `main:app` or enable background loops.
- Poll terminal deployment SUCCESS, inspect bounded sanitized logs, check health,
  signed-owner access, same-origin rejection and persisted records across restart.
- Keep broker submission off until separately provisioned account validation passes.

## Proposed production merge/deployment sequence

- Keep PR #1 draft until blocking gates pass and the complete changeset is reviewed.
- Prepare the exact release SHA, CI run, staging evidence and schema compatibility
  notes; obtain explicit owner approval for merging and production deployment.
- Back up the production SQLite database consistently before any migration.
- Merge/deploy only the approved commit, then verify startup, owner authentication,
  the original trader's account/scanner/monitoring and Pro Ticker isolation.
- Enabling Pro Ticker execution is a separate explicit configuration choice; never
  copy original trader credentials or enable live-money trading.

## Rollback plan

- Record the previous production deployment and database schema before deployment.
- If startup/authentication or isolation checks fail, restore the previous deployment
  using the same production service/environment only after production authorization.
- New Pro Ticker tables are additive; prefer leaving compatible additive tables intact.
  Do not restore an old database over newer fills or broker submission records.
- Any uncertain broker attempt must retain its durable intent/client ID and be
  recovered on the dedicated PAPER account before considering another order.
- A database restore requires independent reconciliation against broker orders and
  activities. Disabling new submissions is preferable to losing financial evidence.

No merge or production deployment is authorized by this document.

## Consistent backup and migration evidence

`backend/backup_paper_database.py` creates a consistent standalone snapshot using
SQLite's backup API, including committed WAL contents and excluding uncommitted
transactions. It uses private file permissions, bounds snapshot time and refuses
to replace an existing backup. It never restores or dispatches broker operations.
`backend/validate_paper_database_copy.py` validates only a disposable copy of an
export: integrity, foreign keys, reservation accounting and original typed column
values for every pre-existing row, including duplicates and blobs. Row counts
alone are insufficient to prove preservation of order/fill evidence.

Synthetic backup/migration tests cover these behaviors. They do not satisfy the
representative deployed export gate; hosted restart evidence is recorded separately above. Never restore an
older snapshot over newer broker-attempt, ownership or fill evidence.

Isolated staging now includes an owner-authenticated, same-origin POST download
at `/staging/database-backup`, accessible from its Staging backup control. The
server chooses the database path; callers cannot supply paths or restore data.
One private temporary snapshot is streamed at a time and removed after delivery
or an interrupted connection. Authentication, no-store headers and sanitized
failures apply. Production does not register this endpoint. The downloaded file
still needs the disposable validator; download success alone is not migration proof.

## Unattended monitoring (2026-10-23)

Set `PRO_TICKER_POSITION_MONITOR_ENABLED=true` only in an explicitly identified
PAPER environment with the existing dedicated account configuration. Default is
off. This flag does not enable owner submission, autonomous buys or budget release.
Both app lifespans use the worker; isolated staging does not start original trader
jobs. Health reports the staging monitor flag and actual background-job state.

Each pass observes up to eight attempted intents, oldest observations first,
with a 30-second cooldown. SQLite claims prevent simultaneous scheduled work on
the same intent for five minutes; abandoned claims expire after a crash.
Existing cumulative fill bridges make retries idempotent. Errors replace previous
protection status with unavailable; observations older than two minutes are stale.
The authenticated `/auto-trader/pro-ticker/monitor-status` endpoint returns the
latest 200 observations. This is a bounded observation window, not full history.
Missing/partial/after-hours protection stays uncertified and requires operator
review. The worker never sends repair, cancel, close or release operations.

Migration: one additive `pro_ticker_monitor_observations` table, created when the
worker first runs. Rollback: disable the flag and restart; the prior application
ignores this additive table. Validate on a representative disposable database
copy before production release. No hosted configuration was changed.

## Owner cancel/close controls

`PRO_TICKER_OWNER_PAPER_MANAGEMENT_ENABLED=true` separately enables authenticated,
same-origin owner controls on the dedicated PAPER account. It does not enable
new buys, autonomous management, or budget release. Runtime/default staging
configuration leaves it off. Each action requires its own browser approval.

The control cancels only the intent's exact pending parent and provenance-linked
bracket exits. A successful DELETE is not cancellation proof: fresh order reads
must establish terminal status. Closing requires a fresh regular-session clock,
terminal entry and exits, cumulative fill reconciliation, exact agreement between
owned shares and broker position, and a complete collision-free open-order read.
The only new sell is a whole-share DAY market request marked sell_to_close with
its own durable ptx client ID. Actual equity support for this field must be checked
against the dedicated PAPER account before enabling the feature.

SQLite claims and persisted dispatch markers precede each network mutation.
Recovery only reads the saved identity, verifies ownership, links/account fills,
and checks closure. Lost responses never cause another POST/DELETE. If interrupted
between the marker and dispatch, or if a close is rejected/canceled with shares
remaining, the one-attempt workflow deliberately stops for review. It must not be
advertised as automatic repair or arbitrary retry. Partial entry cancellation
retains filled shares and all budget; closing a partial entry additionally needs
complete bracket-child evidence. After-hours closes are unavailable.

The additive `pro_ticker_position_actions` table stores claims, attempted IDs,
account fingerprint and close ownership. Manual exits join existing share-ledger,
release-review and execution-journal reconciliation. Closed partial entries use
actual filled shares in priced gross reports. No fees or net profit are invented.
Rollback leaves the additive table intact; disable management/monitor flags before
rolling application code back. Never drop ownership records for an active order.


## Independent market discovery

The Scanner tab now has a separate Discover US market movers action. It queries
one Yahoo US equity screen (up to 250 rows, no market-cap floor), locally filters
canonical US equity symbols, price >= $0.25, average volume >= 50,000, movement
>= 5%, cumulative/full-day volume ratio >= 1.5 and provider timestamps <= 120s old.
It deduplicates/ranks the returned universe and rotates eight detailed Yahoo
minute/daily history checks per run. Detailed 20-session volume/movement/price
and timestamp checks must still pass. This is a bounded provider window, not
coverage of every listed stock or proof of brokerage tradability.

Existing manual/saved-symbol scans remain available. Discovery failures persist
as failures, never silently fall back to saved names, and never fabricate an
empty successful universe. Observations/results survive reload. Saved daily
technical grades are separate from fresh quote/ranking data and clearly dated;
missing grades stay unavailable, with the existing explicit grading handoff.
No discovery request submits any order or enables automatic scanning/trading.

On October 23 a keyless research-only live provider check returned 23 eligible
movers from 250 screened rows; eight detailed checks yielded three accepted
snapshots and five unavailable/filter failures. The check used a disposable DB
and no broker credentials. Counts are one observation, not performance evidence.
Hosted authenticated discovery also completed with six accepted detailed
snapshots from eight checks and two explicit unavailable results. Twenty-six
symbols were eligible in that bounded screen. The scanner-to-manual-grading
handoff selected the stock without submitting a review or order. These are
research-only observations; broker lifecycle certification remains outstanding.


## Execution accounting and complete history export

Fully covered terminal entry/exit FILL activities now count as a completed gross
trade when actual buys equal sells, independently of when its virtual allocation
is returned. Counts distinguish gross wins, losses and breakevens; missing costs
keep fees, adjustments and net P&L null/unavailable. Canceled partial entries use
actual filled quantities. No missing-price record is assigned zero profit.

The authenticated execution-journal.csv export streams every stored activity
from a single read-only SQLite snapshot in 500-row chunks, with a total-count
header and spreadsheet formula escaping. The UI's 500-row display window no
longer limits the full export. Export does not fetch or modify broker orders.

Confirmed zero-fill bracket cancellations have a separate owner-approved release
path: canonical entry ownership, zero accounted entry fills, zero strategy balance,
explicit child references and independently terminal zero-fill children are all
required. A late fill, missing child, pending cancellation, changed approval or
unresolved sell blocks release. Every release still requires a separate owner
attestation and atomic evidence recheck. Unfilled cancellations are classified
not_traded and excluded from trade/win/breakeven/gross-profit statistics.
