# Real-copy execution worklist — November 8, 2026

## Evidence from the deployed report

Read directly from Real Copy Trade on November 8. These are allocated SOL results, not a reconciled account profit or a forecast. There were 197 order records, 44 confirmed buys, no pending orders and no open bot holdings at inspection.

| Source | Scored closed positions | Allocated net SOL after failed fees |
| --- | ---: | ---: |
| Mr. Frog | 5 | +0.006929 |
| Solkrow | 4 | +0.044616 |
| Latuche | 11 | -0.010507 |
| Numero | 1 | -0.419156 |
| Cap | 4 | -0.171028 |
| Iceman | 1 | -0.019570 |
| Kay The Doc | 5 | -0.075922 |
| Johnson | 12 | -0.196622 |

The ledger showed -0.954415 SOL against -0.841260 SOL allocated by source, a -0.113155 SOL gap. Known failed transaction fees were 0.000840 SOL. Historical results do not justify attributing the gap to a particular wallet. The active roster was Mr. Frog, Solkrow, Latuche and 9s4j. No eligibility changes are part of this work.

The activity list also contained repeated Jupiter rate-limit rejections during the ELIZABETH exit. That is an observed execution bottleneck. The available 14 settled sell timing records had a 0.545s median trigger-to-broadcast and 1.974s median trigger-to-observed-finalization; these exclude rejected attempts and do not measure the entire source-sell delay. No historical buy had both recorded source entry cost and source-to-broadcast timing. Do not infer missing prices or claim a before/after improvement from this history.

## Implementation and remaining work

- [x] Review current wallet results and identify missing data.
- [x] Keep live routing normal and leave wallet roster, sizing and fee limits unchanged.
- [x] Add shared Jupiter request pacing and cooldown after HTTP 429 responses. Default to the published free-tier allowance of 1 request/second with a margin; an explicitly verified paid allowance may be set with JUPITER_BUILD_RPS. This does not buy or change a provider plan.
- [x] Implement an independent, bounded normal/fast quote benchmark, with no signer or submission access.
- [x] Compare identical mint, direction, amount and quote policy, alternating which mode is requested first. Exclude output comparisons over five seconds apart.
- [x] Make research yield when there are holdings, pending orders, recent source events or recent live requests. At most one two-request pair per minute, with no automatic retry after a rate limit.
- [x] Expose request latency, minimum token-output differences, successful requests, rate limits and failures in the existing performance report.
- [x] Tag new live orders paced_quotes_v2 so paced execution can be separated from earlier records.
- [x] Pass local backend, syntax and whitespace checks.
- [ ] Pass GitHub desktop/mobile and full-app checks, then merge/deploy the reviewed implementation.
- [ ] Collect real provider samples after deployment. Historical executed amounts are quote-only sample sizes; current holdings and settings are never changed.
- [ ] Reconcile the pre-existing 0.113155 SOL ledger/source gap from receipts; do not repair balances based on this rounded summary.
- [ ] Reassess copied-wallet results as new forward trades settle. Keep small-sample uncertainty visible and preserve owner decisions.
- [ ] Recommend a routing change only after enough matched samples show an acceptable speed/price tradeoff. Benchmark results are quotes, not actual fill or profit evidence. Enabling fast live routing is a separate reviewed change.

## Constraints

The benchmark consumes the existing Jupiter key's API quota and cannot guarantee zero shared-provider contention. It is deliberately low priority and only samples while flat and quiet. Normal live quote requests receive priority; they still fetch fresh executable prices. Local pacing reduces bursts but cannot account for other applications using the same provider account. Existing token-age, source-price, authorization, duplicate, balance and uncertain-submission protections remain in force.
