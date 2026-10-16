# Manual PAPER budget release

`backend/pro_ticker_manual_release.py` is an internal operator service. It has
**no HTTP route, scanner integration, scheduled caller, or automatic approval**.
Adding those requires a separate authentication and authorization integration.
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
   `approved_by` must come from trusted identity, not user-supplied request input.
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

Remaining integration work: authenticated operator review UI, explicit access
control, release-event reporting in history/activity, and isolated staging
validation. Automatic release remains disabled.
