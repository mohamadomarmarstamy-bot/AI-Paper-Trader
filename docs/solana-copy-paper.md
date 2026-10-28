# Solana copy PAPER experiment

`/meme-coins/copy` is a separate virtual account with $100,000 capital and $50 per copied buy by default. It has its own state, permanent signal/close archives, cash, positions, start/stop and settings. It never connects a wallet, signs a transaction, or submits a swap. The existing scanner, its positions, learning batches and settings remain independent.

## Data connection

Set `BIRDEYE_API_KEY` on the server. Do not put it in the browser, repository, URLs or chat. The key's plan must allow these read-only analytics endpoints on `public-api.birdeye.so`:

- GET `/wallet/v2/leaderboard`
- POST `/wallet/v2/pnl/details` (analytics, not execution)
- GET `/trader/txs/seek_by_time`
- GET `/defi/v3/token/meme/detail/single`

No key means no ranked wallets or copied trades. The page displays the missing connection; Start is disabled. Endpoint access and schema are checked on the first run. Paid provider usage may apply. No provider subscription or account is provisioned by this change. Local rolling request budget is 45 calls/minute with a 60-second provider-429 cooldown; polling is at least 10 seconds and cycles do not overlap.

## Prospective selection and copying

Select a fixed cohort of up to five from the provider's top 20 wallets sorted by seven-day realized PnL using WAC. Require positive seven-day and full-history realized profit, full-history reported win rate above 50%, and at least 20 historical sells. Preserve cohort selection times and metrics. The provider's leaderboard has a $100k wallet-value floor, so this is a limited cohort, not all profitable traders. Provider history, labels, accounting and wash-trade claims are not independently audited.

Enroll each wallet at detection time, never backfill old winners. Poll frozen time windows with pagination and a two-second overlap. Buffer incomplete pages before processing them in time order. Deduplicate permanently by wallet, transaction, instruction, inner instruction, mint and side. Resume without copying old paused buys. Each new eligible BUY becomes a separate $50 lot (configurable). A source SELL, even partial, queues a full close of all that wallet's copied lots for the token. Different source wallets remain attributable separately. Owner manual exits carry exact lot identities and queue until a fresh original-pool observation exists; Sell all also pauses entries.

Only provider-identified meme tokens are copied. SOL, USDC and USDT are excluded. Category lookup is solely an asset-type check: **no liquidity, volume, momentum, second-price confirmation, grade, stop, target or scanner gate** controls copy entries. Missing/unsupported data, exhausted request capacity, stale signals over 90 seconds and inadequate virtual cash are explicitly skipped. At most ten new metadata queries are attempted per cycle; more signals show a capacity reason. There is no claim that every wallet action can be captured.

Use fresh positive DEX Screener pool observations after wallet and category lookup; do not use source-trader fills or historical prices. Quotes must have been received within 45 seconds; current marks expire after 120 seconds. Held lots require their original pool; missing exits remain pending. Simulate the same costs as the scanner: 1% and $0.10 per side. These are observations and cost assumptions, not executable quotes or evidence of sellability. Latency and transaction attribution are saved.

A lease and revision CAS protect each cycle. Settings or manual changes invalidate a concurrent simulated cycle, including its event/close writes. Archives commit with cash and positions atomically. Retries cannot duplicate credits. No copy outcomes are fed into the scanner learner.

## Comparison

The common window starts at the first complete positive equity snapshot from both accounts; missing scanner marks do not prevent copy trading. Show each strategy's net P/L change since that snapshot and return on its own starting equity. Exclude virtual funding by differencing equity minus cumulative capital. Copy total P/L is shown separately. Missing marks produce unavailable values and chart gaps. Capital/exposure and trading rules can differ, so this forward observation is not a controlled test or proof of profitability. Up to 2,880 minute points and the latest 100 archived signals/closes are displayed; the archives are permanent.

## Validation

Backend tests cover costs with no liquidity/volume fields, directions, prospective windows, deduplication across restarts/day boundaries, partial-page buffering, source/manual exits, missing original-pool prices, stale signals, CAS races, leases, funding-adjusted comparisons and authenticated same-origin controls. Desktop/mobile tests exercise connection status, controls, isolated writes, saved settings, XSS-safe symbols, chart gaps and queued sells.

Provider contracts: [leaderboard](https://data.birdeye.so/docs/data-api/wallet-networth-pnl/get-wallet-v2-leaderboard), [PnL](https://data.birdeye.so/docs/data-api/wallet-networth-pnl/post-wallet-v2-pnl-details), [trades](https://data.birdeye.so/docs/data-api/transactions/get-trader-txs-seek-by-time.md), [meme metadata](https://data.birdeye.so/docs/data-api/meme/get-defi-v3-token-meme-detail-single.md).
