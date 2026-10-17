# Isolated PAPER staging deployment

Approved scope: a **new staging service and separate volume**, sourced from
`feature/pro-ticker-lab`. Do not modify the existing production service, its
variables, source branch, volume, broker account or deployment settings. Do not
merge the branch. The repository contains a non-default Dockerfile so the
existing service does not adopt the staging entry point accidentally.

## Deployment specification

- Build from repository root with `Dockerfile.paper-staging` selected explicitly.
- Use a separate volume mounted at `/staging-data`; never mount production `/data`.
- Start command is supplied by the Dockerfile. Healthcheck path: `/health`.
- The staging entry point trusts forwarded protocol headers from Railway's
  HTTPS edge so secure cookies and same-origin checks use the public scheme.
  Keep this container behind the hosting HTTP proxy; do not expose it as an
  unproxied public TCP service. Client IP headers are not authorization.
- Keep one replica for the SQLite staging service.
- Use a separate public HTTPS staging hostname and fresh owner credentials.
- Do not copy production environment variables wholesale.

Required nonsecret values:

```
PAPER_STAGING_MODE=true
DATABASE_PATH=/staging-data/trader.db
AUTO_TRADER_START_ENABLED=false
PRO_TICKER_MANUAL_RELEASE_ENABLED=true
```

Set a fresh `APP_ACCESS_PASSWORD` (at least 16 characters) and a fresh
`APP_SESSION_SECRET` (at least 32 characters) through the hosting service's
secure variable interface. Never commit them, put them in workflow logs, or
paste them into chat. Use dedicated Alpaca PAPER credentials for subsequent
broker validation, supplied securely. Do not reuse the actively trading
production PAPER account to create test trades.

The isolated factory registers only login, the research panel, research
intake, budget reads and the guarded manual-release routes. It does not mount
`main.app`, run its lifespan, restore its scheduler, start its background loops,
expose its order controls, or expose live-account routes. Broker order submission
is absent from this staging service. Research quotes and release evidence may
make authenticated read-only PAPER/market-data requests.

## Validation gates

1. Verify `/health` identifies isolated staging and disabled broker submissions
   and background jobs. Confirm only the staging database file is created.
2. Confirm anonymous access is rejected and owner login works over HTTPS.
3. Test the enabled manual operator controls, cross-origin rejection and release
   history against the isolated database; verify production remains unchanged.
4. With dedicated PAPER credentials, fetch actual linked broker orders and test
   reconciliation. Creating real PAPER entry/exit fixtures requires a separate
   controlled test runner and dedicated account; this staging web app cannot
   submit those trades. Partial fills cannot be guaranteed on demand.
5. Obtain a consistent standalone backup of the deployed SQLite database
   (no live WAL beside the exported file). Run
   `python backend/validate_paper_database_copy.py /secure/export.sqlite3`.
   The validator opens the export immutably, migrates a disposable copy in an
   isolated process, checks integrity and reservation accounting, and prints
   only a sanitized result. Never overwrite staging or attach production storage.
6. Review results before any merge or production rollout.

Railway access and secure staging credentials are needed before the deployment
can actually be created. A successful local test or GitHub Actions run is not a
deployment, and a deployed healthcheck is not proof of a completed broker
lifecycle. Report these gates separately.

## Automated container smoke test

The `isolated-staging-container` Actions job builds the actual staging image,
boots it with synthetic credentials and a disposable volume, checks the HTTP
allowlist and disabled background/order flags, and verifies persistence across
a container restart. This is an ephemeral CI check, not a hosted deployment.
