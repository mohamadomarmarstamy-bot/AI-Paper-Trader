# Copy entry timing and price limits

While real copying is enabled, the shared source worker targets a five-second cycle instead of the normal paper polling interval. It checks up to four live-eligible wallets each cycle and reserves a fifth slot for the oldest remaining research wallet. More than four live sources rotate fairly; slow RPC responses can extend the cycle. Existing RPC request budgets remain in effect.

After source events commit, a thread-safe notification wakes the real execution loop. The notification only removes idle waiting: it does not bypass the persisted event cursor, order lease, pending-transaction reconciliation, exit priority, source-sale check, wallet authorization, or two-hour token-age requirement. Funding reads run concurrently and finish before order preparation.

Supported SOL-quoted source receipts carry a reference consisting of raw tokens received and net native SOL plus wrapped SOL spent. The source cost includes network fees, tips, swap fees, and account rent. It is a conservative all-in cost reference, not a claim to the source's fee-free market price. Other changing token balances, missing balances, non-SOL quote assets, or mismatched decimals cannot provide a reference. Unchanged pre-existing cash balances do not invalidate one.

New real buys must have a minimum executable token output that keeps SOL per token within 5% of that source reference. The check runs during every preparation, including fresh-quote retries, and again before initial broadcast or recovery of an unsent signed order. The quote's minimum output already includes allowed slippage, so increasing slippage cannot relax this bound. Missing references reject new buys. Already-broadcast transactions continue normal reconciliation and existing holdings retain their exit rules.

Buy activity exposes source-to-detection, saved-signal-to-execution, funding, preparation, broadcast, and observed finalization timings. Source block times have second precision. Finalization timings include receipt polling and do not claim exact network confirmation latency.

Validation uses isolated ledgers, simulated signing, source-receipt accounting tests, missing/mismatched-reference rejection, worst-case quote boundaries, recovery checks, fair polling, and event-driven execution wakeup. No validation submits a live trade.
