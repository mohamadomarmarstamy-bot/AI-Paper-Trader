# Supervised dedicated PAPER lifecycle test

This procedure validates actual broker behavior. Mocked tests and healthy HTTP
responses do not satisfy it. Use isolated staging and the already configured
dedicated PAPER account. Do not use the original trader or a live endpoint.

## Before enabling submission

- Confirm the exact deployed app commit passed CI and staging health is healthy.
- Verify dedicated account identity/readiness through Settings & Health.
- Inspect the dedicated broker account for existing orders and positions. Resolve
  any unexplained activity before beginning; do not adopt it into this test.
- Keep automatic buying, automatic budget release and unattended monitoring off.
- Work during an open regular session with time to observe and finish the test.
  Owner close controls deliberately reject closed or stale session clocks.
- Use one whole share, with an entry ceiling no greater than $25. This is an
  entry-cost bound, not a guaranteed maximum loss or an after-hours exit promise.
  Do not expand size or budget to make a rejected candidate pass.

## Research and allocation

1. Check the candidate's latest broker reference using the explicit read-only
   button. Passing age alone does not approve an order. Stale or unavailable
   evidence means stop; do not modify timestamps or substitute Yahoo prices.
2. Capture a valid saved BUY review, then save a one-share draft with explicit
   stop and target assumptions. Preserve the saved grade and original evidence.
   A historical draft's price and risk ceilings remain binding.
3. Explicitly allocate the draft once. Check that exactly one intent/reservation
   exists and that the virtual committed amount matches its saved ceiling.
   Drafts reserve nothing; allocation does not create a broker order.
4. Run fresh admission. Account, session, asset, positions, open orders, reference,
   budget and current risk settings must all pass. Reference limits remain
   90 seconds for allocation and 30 seconds for submission admission.

## Submit and verify

5. Enable only the dedicated owner submission and management flags for this
   supervised test. Keep monitoring off. Recheck hosted health after deployment;
   feature flags do not constitute the per-order owner checkbox confirmation.
6. Confirm and submit the single prepared intent once. Submission rechecks fresh
   admission. Save the intent identity and its broker client/order identities in
   private test evidence, never public issues or documentation.
7. Inspect the resulting broker order and independently recover/reconcile it.
   A pending response, 404 lookup or transport error is an uncertain result,
   not permission to buy again. Reuse existing-order recovery and preserve funds.
8. Verify actual fill quantity and both native bracket legs against independent
   broker snapshots. A submitted entry is not proof of a fill or active protection.
   Partial bracket entries are not certified as protected. Do not create further
   entries while any fill, ownership or protection question remains unresolved.

## Close, reconcile and release

9. For an unfilled entry, explicitly cancel and independently confirm terminal
   entry/child states with zero fills. Cancellation acceptance alone is insufficient.
10. For filled shares, use the explicit owner close control. It confirms old exits
    terminal, reconciles racing fills, compares the remaining owned quantity to the
    broker position and attempts one saved-identity close. Do not send a second
    sell after a lost response; inspect/recover the existing close instead.
11. If cancellation, closure or protection cannot be verified, stop further test
    entries and inspect the dedicated account directly. Preserve accounting and
    approvals. Do not switch off management while an unresolved position needs it.
    An independently submitted broker intervention requires reconciliation; it is
    not automatically recognized as an owned strategy exit.
12. Confirm zero remaining owned shares and no active related broker orders. Sync
    priced execution activities and verify per-fill price/time/quantity coverage
    agrees with cumulative broker fills and the strategy share ledger. Incomplete
    activity pages must fail coverage rather than report complete performance.
13. Review and explicitly approve virtual budget release only after independently
    verified terminal evidence. Confirm the released amount, zero residual
    commitment and immutable release history. Returned allocation is not profit.
14. Export the complete execution journal. Check gross entry/exit arithmetic;
    fees, net P&L and adjustments remain unknown without verified cost evidence.
15. Once the account is flat, related orders are terminal and evidence is saved,
    disable owner submission and management again. Verify hosted health and
    independent workspace readback. Leave automatic release and monitoring off.

## Pass record

Record the deployed commit, UTC observation times, admission outcome, actual
entry/exit quantities, protection observations, recovery behavior, priced activity
coverage, release result and final account/order state privately. Mark a step
uncertified if it was not observed. A normal one-share fill cannot certify partial
fills, after-hours protection, lost-response behavior or multi-page real activity.
Those cases remain distinct release gates even when this basic lifecycle passes.

No production merge or deployment is authorized by this procedure.
