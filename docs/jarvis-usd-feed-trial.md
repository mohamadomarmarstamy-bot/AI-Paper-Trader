# USD candle trial — credential and entitlement gate

The comparator remains disconnected from trading. No paid plan has been purchased, no key is configured, and no live one-minute comparison has passed.

Candidate: DexPaprika exact-pool USD OHLCV. Current official pricing lists Dev at $30/month excluding applicable tax, 500,000 credits/month, 120 requests/minute, one-minute candles and 30 days of history. Free registered keys only allow ten-minute and longer candles. A public exact-pool test succeeded for metadata and returned HTTP 403 for one-minute OHLCV, confirming the access gate. A paid plan or explicitly entitled evaluation key is required before testing this path. Do not promise a free trial or freshness without actual account entitlement and data.

Prepared command for an operator on the server:
`python backend/jarvis_usd_feed_trial.py pools.json`
The key must be supplied privately as JARVIS_DEXPAPRIKA_TRIAL_KEY; never in command arguments or chat. The command is not scheduled and does not auto-enroll, purchase or enable a fallback. It accepts 1–6 distinct public mint/pair objects, validates the entire sample before requests, and uses at most two requests per pool. It stops on 401/403/429 and exposes fixed error labels, not raw provider errors or credentials.

The checker pins api-pro.dexpaprika.com and Solana, verifies returned pool and explicit base/quote token identity, selects inversed according to the requested token, and uses the documented USD price candles. It validates timezone-aware one-minute interval boundaries and rejects duplicates, stale or gapped history under existing validation. No candles are synthesized. Provider pool-volume denomination is not sufficiently documented here, so volume equivalence is explicitly unverified. Results cannot feed the trading pipeline.

Acceptance before connection: fresh contiguous candles for representative exact pools; verified USD close prices and token orientation; validated volume units and comparable RSI/volume signals over matching windows; an agreed monthly budget and persistent quota limiter; then a separately reviewed fallback change. Synthetic tests are not a live data validation.

Other findings: Birdeye's pair endpoint does not accept the token endpoint's currency parameter; v_usd documents volume, not by itself price denomination. Do not treat the existing operator benchmark's pair OHLC prices as proven USD without additional verification. Moralis offers explicit currency selection but its current public pricing lists API plans starting at $149/month. Dexploit SOL prices remain incompatible with a direct USD substitution.

Sources checked November 25, 2026:
https://docs.dexpaprika.com/api-reference/pools/get-ohlcv-data-for-a-pool-pair
https://docs.dexpaprika.com/api-reference/pools/get-a-pool-on-a-network
https://dexpaprika.com/api/pricing
https://data.birdeye.so/docs/data-api/price-ohlcv/get-defi-v3-ohlcv-pair
https://moralis.com/pricing/
