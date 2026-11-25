# Alternate candle feed trial — not connected to trading

Prepared November 25, 2026. Related issues: #157; primary feed recovery #161.

The service has no configured Birdeye, Moralis, DexPaprika or Dexploit credentials. No account was created, subscription started, or private key requested in chat. A live authenticated comparison is blocked until the owner supplies an evaluation key through Railway's private variables.

Candidate evaluation:
- DexPaprika's current official free tier allows 10-minute and longer candles, not the one-minute bars required here. Do not substitute those intervals.
- Dexploit's documentation advertises a free key and one-minute pool candles, but its pricing/home/documentation pages conflict on quotas and history. Confirm the actual dashboard entitlement. Budget a bounded trial, not continuous polling.
- Dexploit prices are SOL per token and volume_sol is lamports. They must not enter Jarvis's USD chart pipeline. Matching USD historical normalization and indicator equivalence would require separate validation before any trading fallback.

`jarvis_alternate_feed_check.benchmark(rows, key)` accepts 1–12 distinct exact pools, makes at most one GET per pool, stops on 401/403/429, returns fixed outcome labels with no credentials, and never reads or modifies trader state. It validates token/pool/timeframe identity, timezones, contiguous completed bars and existing candle freshness. Empty intervals are not invented. No live trial has been run; four synthetic tests cover safety/validation behavior only.

Next required input: an evaluation API key, entered privately into Railway, e.g. JARVIS_DEXPLOIT_TRIAL_KEY. This module deliberately does not auto-enable or auto-read that variable: a subsequent server-side bounded runner must explicitly invoke it. No laptop change is needed. Do not claim trading recovery or deploy a fallback based on these synthetic tests.

Sources checked:
https://docs.dexploit.dev/
https://dexploit.dev/blog/how-to-get-solana-dex-candles-on-a-free-api-key
https://dexploit.dev/pricing
https://dexpaprika.com/api/pricing
