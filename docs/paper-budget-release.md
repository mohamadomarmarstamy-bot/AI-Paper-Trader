# Manual PAPER budget release

`backend/pro_ticker_manual_release.py` is an operator service behind the
opt-in `pro_ticker_release_routes` router. It has **no scanner integration,
scheduled caller, or automatic approval**. The app has one owner login; this
is not a multi-user authorization system. All approver identities are supplied
by the server after authenticating the signed app-owner session.
It only changes Pro Ticker virtual allocations; it never submits broker orders,
changes broker cash, or accesses live trading endpoints.

## Approval boundary

1. A trusted caller binds a PAPER-only order reader (the existing
   `make_alpaca_paper_order_reader` pins HTTP GET requests to the PAPER host and
   rejects redirects). Never accept an order reader or broker evidence from an
   HTTP request body.
2. `prepare_manual_paper_release` fetches every linked entry/exit and verifies
   terminal orders, exact ownership and persisted fills. It checks the symbol
   position is zero and its fill balance agrees. It stores a canonical evidence
   snapshot and SHA-256 digest in a review expiring after five minutes.
3. An authenticated operator independently checks PAPER account provenance and
   the exact review digest before calling `approve_manual_paper_release`.
   The router sets `approved_by` to `authenticated-app-owner`; request bodies
   cannot set an actor or provide broker snapshots. Same-origin JSON and the
   explicit operator header are required for both write endpoints.
   The explicit attestation is an operator assertion, **not cryptographic proof
   supplied by an injected transport**. Test callbacks never establish automatic
   authority.
4. `release_manually_approved_paper_budget` fetches all orders again. Under
   `BEGIN IMMEDIATE` it repeats closure verification and compares the full
   evidence digest. Changed accounting, links, fills, order states or expired
   approval require a new review.
5. The release event, reservation transition, and approval consumption commit
   together. Exceptions roll back all three. Retrying the same review returns
   the existing release event without another release or broker request.

Entry orders canceled/expired after partial fills are supported when every
filled share has a terminal, owned exit. Unfilled entry remainder is never
counted as a position. Multiple exit orders and zero-fill terminal exits are
checked individually; a live linked order prevents release even if the posted
share balance is zero. Exact reservation accounting is required; absence from
a broker position list is never sufficient proof.

The service conservatively blocks a nonzero symbol-level strategy position.
It does not close positions held by other strategies, reconcile cash/P&L, or
modify the existing auto-trader. Released notional returns to the virtual
budget; this is not realized profit. The durable event records reservation,
review, digest, operator, cents and timestamp. The corresponding review retains
the canonical evidence without API credentials or unrelated broker fields.

## Validation

Run `python -m unittest discover -s backend -p 'test*.py' -v`.
The manual-release integration suite covers partial entries, split exits,
PAPER-host GETs, retries, concurrent requests, expiry, changes during fetch,
rollback after an injected database interruption, evidence retention and
rejection of ownership mismatches. All use temporary SQLite and synthetic
broker responses. No real account has been used to exercise this service.

## App controls and history

The Pro Ticker panel displays virtual budget release history separately from
trade P/L. GET `release-status` and `release-events` under
`/auto-trader/pro-ticker-paper` require an app session. History reads never
initialize tables or release funds. The operator UI requires preparation,
independent verification of the displayed PAPER orders, a checkbox, and a
separate confirmation click. Pending or unlinked broker orders must also be
reviewed by the operator; this service cannot discover ownership of unlinked
orders.

POST `release-review` and `release-confirm` are disabled unless
`PRO_TICKER_MANUAL_RELEASE_ENABLED` equals the literal `true`. No deployed
setting has been changed. Enable only in an explicitly approved isolated
PAPER staging environment before production consideration. Request bodies
cannot inject transport, broker data, or an operator identity. Retries of the
same committed confirmation return the existing event, even if broker
credentials are temporarily unavailable. Automatic release remains disabled.

Remaining validation: isolated staging with real PAPER credentials and a
representative deployed-schema copy. A real-browser visual check also remains;
local headless browser installation was unavailable. Synthetic HTTP, DOM,
production-app smoke and concurrency tests do not replace those checks.
