# Archived GOON manual-sale attribution

## Finding

The previously observed ledger/source difference of **-113,155,034 lamports** exactly matches the archived November 1 GOON manual-sale repair:

| Component | Lamports | SOL |
| --- | ---: | ---: |
| Recorded remaining entry cost | 212,834,386 | 0.212834386 |
| Net proceeds across five finalized receipt legs | 99,679,352 | 0.099679352 |
| Previously posted realized adjustment | -113,155,034 | -0.113155034 |

The operator repair in commit `ac459c7` (`scripts/reconcile_goon_manual_sale.py`, PR #75) stores this in `solana_real_run.state_json.accounting_repairs`. It updates runtime realized P/L atomically but does not create a row in `solana_real_orders`. The source-allocation reports counted sell orders and failed fees, omitting this archive format. This explains the exact observed mismatch when that archived proof is present. Production verification remains necessary after deployment; the implementation never fills a difference merely because its amount matches.

## Correction

Reports read the existing archive without modifying it. Attribution requires the reviewed repair identity, wallet, mint, exact amounts, original adjustment, all five signatures in order, receipt dates/slots, net SOL movements and exact token reduction. The original confirmed buy must establish the source wallet and matching remaining quantity/cost after any earlier recorded sales. An overlapping already represented sale is counted once; duplicates, missing evidence, unrelated wallet entries and unresolved/open costs remain flagged.

The report shows a bridge from ordinary sale P/L minus failed transaction fees, through previously posted manual adjustments, to the recorded ledger total. An archived manual exit may complete a scored position, but it never increments the confirmed-sell or fill count. Entry and manual-sale fees are already included in their receipt costs/proceeds; a separate failed bot transaction fee is deducted exactly once.

The live ledger, balances, trade history, order status, bot enablement, sizing, routes and wallet eligibility are not changed. The old one-off repair must not be run again to close this reporting gap.

## Validation and rollout

- Exact five-receipt reproduction reconciles the 113,155,034-lamport omission without any database writes.
- Tests cover separate failed fees, partial exits/cost rounding, duplicates, receipt overlap, missing/tampered proof, wrong owner/coin/entry, and both source reports.
- Desktop/mobile report checks cover the reconciliation bridge and receipt links.
- After merge/deployment, inspect **Trade performance → Accounting reconciliation**. Confirm the archived GOON allocation points to its original source wallet and the remaining difference is exactly zero. If proof is absent or inconsistent, retain the warning and investigate the remaining records.
