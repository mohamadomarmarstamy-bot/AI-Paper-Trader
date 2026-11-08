# Copy entry timing and price limits

While real copying is enabled, the shared source worker targets a five-second cycle instead of the normal paper polling interval. It checks up to four live-eligible wallets each cycle and reserves a fifth slot for the oldest remaining research wallet. More than four live sources rotate fairly; slow RPC responses can extend the cycle. Existing RPC request budgets remain in effect.

After source events commit, a thread-safe notification wakes the real execution loop. The notification only removes idle waiting: it does not bypass the persisted event cursor, order lease, pending-transaction reconciliation, exit priority, source-sale check, wallet authorization, or two-hour token-age requirement. Funding reads run concurrently and finish before order preparation.

Supported SOL-quoted source receipts carry a reference consisting of raw tokens received and net native SOL plus wrapped SOL spent. The source cost includes network fees, tips, swap fees, and account rent. It is a conservative all-in cost reference, not a claim to the source's fee-free market price. Other changing token balances, missing balances, non-SOL quote assets, or mismatched decimals cannot provide a reference. Unchanged pre-existing cash balances do not invalidate one.

New real buys must have a minimum executable token output that keeps SOL per token within 5% of that source reference. The check runs during every preparation, including fresh-quote retries, and again before initial broadcast or recovery of an unsent signed order. The quote's minimum output already includes allowed slippage, so increasing slippage cannot relax this bound. Missing references reject new buys. Already-broadcast transactions continue normal reconciliation and existing holdings retain their exit rules.

Buy activity exposes source-to-detection, saved-signal-to-execution, funding, preparation, broadcast, and observed finalization timings. Source block times have second precision. Finalization timings include receipt polling and do not claim exact network confirmation latency.

Validation uses isolated ledgers, simulated signing, source-receipt accounting tests, missing/mismatched-reference rejection, worst-case quote boundaries, recovery checks, fair polling, and event-driven execution wakeup. No validation submits a live trade.

## Live wallet notifications and execution RPC

Execution now selects `SOLANA_REAL_RPC_URL` when explicitly configured, otherwise the existing `SOLANA_COPY_RPC_URL`, otherwise the public mainnet fallback. Invalid configured URLs fail rather than silently switching providers. Each execution tick retains the read-only mainnet genesis check before any order. The connection diagnostic shows which configuration is selected and whether that endpoint passed a recent mainnet check; it does not imply that the provider's plan permits transaction submission. A rejected or uncertain send never triggers a submission to a different provider.

The source worker subscribes to confirmed `logsSubscribe` notifications for live-eligible RPC wallets and sources of existing real positions. Sources needed for exits stay watched when new buys are paused or the paper account is flat. Paper-only research sources are excluded. A notification with a successful supported Pump/PumpSwap program invocation wakes an HTTP history check for that wallet. It does not supply a fill, advance a durable cursor, bypass the two-hour age check, or bypass the 5% entry cap. Duplicate signatures are coalesced.

The WebSocket URL uses `SOLANA_COPY_WS_URL` if supplied, otherwise the existing copy RPC URL with `https` changed to `wss` on the same host/path/query. Providers that use a different stream address require the explicit override. Only encrypted URLs are accepted and redirects are not followed. Provider exception text and credential-bearing endpoints are never included in stream status or logs.

Notifications are optional acceleration: the normal full polling cadence continues, with targeted notification cycles between full sweeps and a one-second burst coalescing floor. Existing read request budgets remain enforced. Streams reconnect with bounded backoff, resubscribe, and wake HTTP reconciliation from persisted cursors. Subscription support, mainnet read access, and transaction-submission permission are distinct capabilities and must be verified after deployment against the configured provider. No new provider account or subscription is created by this change.
