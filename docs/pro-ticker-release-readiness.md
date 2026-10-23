# PAPER release preparation — approval is still blocked

## Current scope

- Dedicated workspace, owner-entered grading, persisted research and proposals.
- Rotating, operator-triggered market scans of saved/entered symbols using the
  existing keyless Yahoo dependency. Stale/missing data is explicit.
- Dedicated-account owner-confirmed limit brackets, durable dispatch claims,
  at-most-once POST attempts, exact-client-ID recovery and reservation accounting.
- Owner-triggered bracket entry/exit reconciliation and protection observations.
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
   Railway OAuth currently requires reconnection; no production diagnosis is claimed.
2. Obtain a separate Alpaca PAPER account and confirm its identity differs from the
   original trader's account. Never use original account credentials in staging.
3. Complete exact-commit CI and review screenshots, then deploy to the already
   isolated staging service/volume. Verify authenticated APIs and configured gates.
4. Save an identifiable owner record, restart isolated staging, and independently
   read the record afterward. A local app recreation is not hosted restart proof.
5. Validate a representative exported database on a disposable copy. The original
   export must remain unchanged; no representative export has been supplied.
6. Validate bracket behavior, account activity pagination, partial fills and provider
   freshness against the dedicated account. Synthetic evidence is insufficient.
7. Resolve first-release limits: no unattended Pro Ticker monitor scheduler, no
   application manual bracket cancel/close workflow, and no independent expanding
   scanner universe. Bracket entries can be unprotected while partially filled;
   protection is not certified after hours. These must be addressed or explicitly
   accepted as restricted operator-supervised scope before production approval.
8. Net P&L, fees, corrections, corporate actions and broader risk-adjusted analytics
   remain unverified. Do not advertise research grades as AI win probabilities.

## Staging deployment checklist (authorized, pending connection)

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
