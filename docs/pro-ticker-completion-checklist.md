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
- [x] Proposal requires owner session, same-origin JSON; never reserves budget or submits orders.
- [x] Journal symbol/date filters and CSV export of posted share events.
- [x] Reuse manual research intake and manual release approval/history controls.
- [x] Scoped responsive styling; keyboard tab navigation and refresh/error states.

## Highest-priority remaining integration

- [ ] Dedicated discovery job with persisted run status, errors, fresh quote/indicator snapshots and candidate diversity.
- [ ] AI research evaluation with provenance, bullish/bearish evidence, regime and supported news sources.
- [ ] Persist verified execution prices, actual execution timestamps and reservation attribution in the fill journal.
- [ ] Compute realized P/L, unrealized marks, equity series, drawdown and cost-adjusted metrics only from verified priced data.
- [ ] Wire proposals to atomic reservations, independently checked broker admission and authorized PAPER submission.
- [ ] Persist execution intent before network calls, resolve interrupted submissions by client order ID, and verify protective orders.
- [ ] Connect entry/partial exit/complete exit reconciliation and release review end-to-end through HTTP/UI.
- [ ] Keep automatic release disabled unless separately approved.
- [ ] Add strategy-specific editable settings with authenticated same-origin safeguards.
- [ ] Link journal outcomes and forward research to measured strategy/regime comparisons.

## Validation and external blockers

- [ ] Complete workflow must pass end-to-end tests; module coverage alone is insufficient.
- [ ] Validate a representative standalone exported database on a disposable copy. No export has been supplied.
- [ ] Verify a saved record through an actual hosted restart. Owner login value is redacted to the connector; do not reset it or expose it to obtain test access.
- [ ] Dedicated Alpaca PAPER account validation. The owner currently has one actively used PAPER account; do not reuse it for staging tests.
- [ ] New staging deployment requires explicit approval. Existing hosted staging remains pinned to its previously approved build; pushing the feature branch is not deployment approval.
- [ ] Full PR review and explicit merge approval. Keep PR #1 draft.

## Interpretation

The workspace shows a configured virtual allocation, not a dedicated brokerage
account. Share fill events currently have no execution price, so performance is
unavailable. Candidate rows are historical saved research, not an operational
independent scanner. Proposal calculations use operator estimates, are not
broker-approved plans, and do not claim a stop will cap actual losses. No chart
or measured strategy improvement is fabricated to fill missing integrations.
