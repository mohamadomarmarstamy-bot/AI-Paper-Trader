# Solana copy PAPER experiment

`/meme-coins/copy` is a separate virtual account with $100,000 capital and $50 per copied buy by default. It has its own state, permanent signal/close archives, cash, positions, start/stop and settings. It never connects a wallet, signs a transaction, or submits a swap. The existing scanner, its positions, learning batches and settings remain independent.

## Data connection

Open **Profile** at `/meme-coins/profile` to save your name, optional public Solana wallet address and Birdeye API key. The wallet address is a profile detail; it does not change the copied-trader selection or connect a wallet. Never enter a seed phrase or wallet private key. Blank API-key input preserves the existing key; replace by pasting a new key, or explicitly remove it. Saving does not certify provider access. If copying was already enabled and waiting for a key, a working key lets discovery begin automatically.

The owner-only, same-origin profile endpoint returns connection state, never the saved key. Credentials are Fernet-encrypted in a separate singleton profile table. A generated owner-readable-only encryption key is kept alongside the persistent database as `<DATABASE_PATH>.profile-key`; retain this file securely with the volume across redeployments, and back it up separately from database exports. Losing it requires replacing the API key in Profile. The browser never stores the key in local/session storage or repopulates it. Profile updates use revision checks to prevent stale-tab overwrites. An existing server `BIRDEYE_API_KEY` is used until a profile key is saved or explicitly removed; removal suppresses that fallback. Do not place API keys in the repository, URLs or chat. The key's plan must allow these read-only analytics endpoints on `public-api.birdeye.so`:

- GET `/trader/gainers-losers`
- POST `/wallet/v2/pnl/details` (analytics, not execution)
- GET `/trader/txs/seek_by_time`
- GET `/defi/v3/token/meme/detail/single`

No key means no ranked wallets or copied trades. The page displays the missing connection; copying can be enabled to wait for the key. It makes no provider request or PAPER buy until a key is available. Endpoint access and schema are checked on the first run. Paid provider usage may apply. No provider subscription or account is provisioned by this change. Local rolling request budget is 45 calls/minute with a 60-second provider-429 cooldown; polling is at least 10 seconds and cycles do not overlap.

## Prospective selection and copying

Continuously page through Birdeye realized-profit gainers across 1W, 30d and 90d windows, 100 candidates per page up to the documented 10,000 offset range, then rotate windows. This replaces the old incorrectly parameterized wallet-value leaderboard. Rankings require at least $1,000 realized profit under the provider contract. Qualify two pending candidates per cycle with full-history positive realized profit, win rate above 50% and at least 20 sells. Enroll every qualifying discovered wallet; there is no five-trader cap. Preserve each enrollment timestamp, ranking window, qualification metrics and frozen swap provider. Failed qualification checks remain queued for retry. Existing checked wallets can be marked inactive for entries; owned lots retain sell monitoring. This is provider-limited historical discovery, not a claim to identify every trader or predict future winners.

Poll up to three wallets per cycle in oldest-polled order, independently of lexical wallet order, and check up to two fresh meme metadata records per cycle. This reserves space within the existing 45 Birdeye calls/minute budget. Large populations take longer to revisit and signals over 90 seconds old are explicitly skipped; the UI shows a nominal sweep estimate. There is no claim to capture every buy or sell.

Enroll each wallet at detection time, never backfill old winners. Poll frozen time windows with pagination and a two-second overlap. Buffer incomplete pages before processing them in time order. Deduplicate permanently by wallet, transaction, instruction, inner instruction, mint and side. Resume without copying old paused buys. Each new eligible BUY becomes a separate $50 lot (configurable). A source SELL, even partial, queues a full close of all that wallet's copied lots for the token. Different source wallets remain attributable separately. Owner manual exits carry exact lot identities and queue until a fresh original-pool observation exists; Sell all also pauses entries.

Only provider-identified meme tokens are copied. SOL, USDC and USDT are excluded. Category lookup is solely an asset-type check: **no liquidity, volume, momentum, second-price confirmation, grade, stop, target or scanner gate** controls copy entries. Missing/unsupported data, exhausted request capacity, stale signals over 90 seconds and inadequate virtual cash are explicitly skipped. At most two new metadata queries are attempted per cycle; more signals show a capacity reason. There is no claim that every wallet action can be captured.

Use fresh positive DEX Screener pool observations after wallet and category lookup; do not use source-trader fills or historical prices. Quotes must have been received within 45 seconds; current marks expire after 120 seconds. Held lots require their original pool; missing exits remain pending. Simulate the same costs as the scanner: 1% and $0.10 per side. These are observations and cost assumptions, not executable quotes or evidence of sellability. Latency and transaction attribution are saved.

A lease and revision CAS protect each cycle. Settings or manual changes invalidate a concurrent simulated cycle, including its event/close writes. Archives commit with cash and positions atomically. Retries cannot duplicate credits. No copy outcomes are fed into the scanner learner.

## Comparison

The common window starts at the first complete positive equity snapshot from both accounts; missing scanner marks do not prevent copy trading. Show each strategy's net P/L change since that snapshot and return on its own starting equity. Exclude virtual funding by differencing equity minus cumulative capital. Copy total P/L is shown separately. Missing marks produce unavailable values and chart gaps. Capital/exposure and trading rules can differ, so this forward observation is not a controlled test or proof of profitability. Up to 2,880 minute points and the latest 100 archived signals/closes are displayed; the archives are permanent.

## Validation

Backend tests cover costs with no liquidity/volume fields, directions, prospective windows, deduplication across restarts/day boundaries, partial-page buffering, source/manual exits, missing original-pool prices, stale signals, CAS races, leases, funding-adjusted comparisons and authenticated same-origin controls. Desktop/mobile tests exercise connection status, controls, isolated writes, saved settings, XSS-safe symbols, chart gaps and queued sells.

Provider contracts: [leaderboard](https://data.birdeye.so/docs/data-api/wallet-networth-pnl/get-wallet-v2-leaderboard), [PnL](https://data.birdeye.so/docs/data-api/wallet-networth-pnl/post-wallet-v2-pnl-details), [trades](https://data.birdeye.so/docs/data-api/transactions/get-trader-txs-seek-by-time.md), [meme metadata](https://data.birdeye.so/docs/data-api/meme/get-defi-v3-token-meme-detail-single.md).

## Solscan integration

Save an optional **Solscan Pro API key** in Profile. The encrypted key has the same write-only and revision-protected behavior as Birdeye, with independent replace/remove controls and a server `SOLSCAN_API_KEY` fallback. Newly enrolled wallets choose Solscan swap history when configured; existing wallets stay pinned to their original provider, including through pause/resume. Key removal or provider errors never silently switch sources and duplicate trades. Birdeye remains required for profit rankings, full-history qualification and meme classification. Solscan account leaderboards rank asset values, not realized profit.

The read-only adapter calls `https://pro-api.solscan.io/v2.0/account/defi/activities` using the `token` authentication header. Frozen time windows and bounded 100-row pages are buffered before interpretation, with a separate 30-call/minute local budget and 429 cooldown. Validate the wallet actor, swap type, signature, timestamp and positive token amounts. Prefer one aggregate swap over its direct route records; ambiguous multiple independent swaps/routes are not copied. Deduplicate canonical transaction/asset/direction events permanently through the existing journal. This intentionally misses unsupported transactions rather than inventing direction. The adapter never connects a wallet or submits a transaction. Wallet links lead to Solscan for inspection.

Contracts: [Birdeye gainers](https://data.birdeye.so/docs/data-api/wallet-networth-pnl/get-trader-gainers-losers), [Solscan swap history](https://pro-api.solscan.io/pro-api-docs/v2.0/reference/v2-account-defi-activities), [Solscan authorization](https://pro-api.solscan.io/pro-api-docs/v2.0/docs).

## Owner-requested research follows and read-only connection checks

`POST /meme-coins/copy/connections` requires the owner session, same-origin JSON and an empty body. It performs bounded, fixed-host requests for Birdeye realized-profit discovery, wallet P/L, meme metadata and swap history, plus Solscan swap history. It saves only safe status summaries/counts, not credentials or raw provider payloads, and creates no trades. HTTP 401/403 errors now identify the denied Birdeye endpoint. A successful request does not certify profitability or an end-to-end fill.

`POST /meme-coins/copy/seeds` accepts the current settings revision and enrolls the two specifically reviewed PAPER research wallets below, idempotently and at the current time. Existing cursors, lots, cash and histories are preserved. Solscan is chosen when configured; otherwise the source is Birdeye. Historical buys are never backfilled. These explicitly requested research follows are labeled separately from the automatic full-history qualification policy; their source reports are not independent audits or future-profit claims. They still require accessible swap history, meme metadata, fresh quotes, sufficient virtual cash and the existing deduplication/ledger rules.

Reviewed on 2026-10-28, using KOL Explorer's publisher-reported 90-day closed-position data:

- Mr. Frog — `4DdrfiDHpmx55i4SPssxVzS9ZaKLb8qr45NKY9Er9nNh`; approximately $377,000 realized, 4,763 closed positions. Source: https://kolexplorer.com/kol/mr-frog
- Numero — `A3W8psibkTUvjxs4LRscbnjux6TFDXdvD4m4GsGpQ2KJ`; approximately $65,100 realized, 310 closed positions. Source: https://kolexplorer.com/kol/numero

Source numbers are preserved as external research evidence, not the app's full-history P/L, sell count or win-rate fields. Automatic discovery continues independently when the provider plan permits it. Enrolling research follows cannot repair an invalid API key or missing endpoint entitlement; connection checks must report those blockers honestly.


## Phantom and key-free PAPER source

Profile supports the injected Phantom Solana provider on HTTPS and Phantom’s mobile browser. Connect reads the public address into the form; Save profile persists it. Account changes update the unsaved form, and disconnecting never deletes the saved address. This is not wallet ownership authentication. No message signing, spend authority, secret seed, or transaction submission is requested. Advanced API keys remain encrypted and optional for key-free mode.

Enable key-free PAPER copying explicitly migrates followed wallets without open lots to public Solana RPC, preserving cash/history and resetting only those source cursors prospectively. A wallet with open copied lots cannot change source. Existing RPC cursors are preserved on repeated clicks. New research follows use RPC after this mode is selected. Phantom itself does not supply blockchain history or rankings.

The RPC client permits only getSignaturesForAddress/getTransaction at https://api.mainnet.solana.com, with fixed host, no redirects, five-second requests, response-size checks, 100 requests/minute and a 60-second cooldown on 429. It takes up to 20 recent signatures per wallet window and reads four transactions per wallet per cycle. Results are buffered until a frozen window is complete; unsupported transactions and truncated windows are visible in status. Unsupported programs and ambiguous multi-swap routes are skipped. This limited public endpoint has no production availability guarantee.

The parser recognizes only pinned Pump.fun/PumpSwap buy/sell discriminators and IDL account indexes from https://github.com/pump-fun/pump-public-docs/tree/main/idl (snapshot 2026-10-30). It requires a successful transaction, matching signature, signed followed-wallet user account, supported cash quote, and matching net owned token balance change. Transfers, unrelated token flows and multi-swap transactions are skipped. Platform provenance does not independently establish meme status, safety, sellability or trader profitability. It is displayed separately from Birdeye’s meme classification. A fresh DEX Screener price is still required for every PAPER fill; missing original-pool exits remain unresolved.

The optional Birdeye profit-discovery failure is now separate from followed-wallet polling errors, so denied rankings cannot stop public-RPC follows or claim their data works. Connection tests cover the public endpoint as well as the advanced providers, without creating orders.


## Repeat-entry protection and continuous public research discovery

Distinct source add-on transactions previously opened independent full-size copied lots. New entries now allow only one open position per mint across followed wallets. Supported RPC decoding preserves the source's pre-swap token balance, so an add to an existing source holding is skipped even if no copied lot exists. Every copied exit starts a configurable 30-minute default mint cooldown, persisted across restarts. The first upgraded cycle seeds this cooldown from recent historical closes. Existing duplicate lots remain intact and source sells/manual exits still close and account for them. Costs remain 1% slippage plus $0.10 per side.

With key-free PAPER mode and automatic discovery enabled, the public KOL Explorer board is refreshed every ten minutes independently of Birdeye authorization. Only the exact case-sensitive `data-address` is used; the lowercased search key is never treated as a wallet. Default eligibility requires positive reported 7/30-day P/L, reported win rate over 50%, median holding time at least 60 seconds, at least 50 reported transactions, and activity within seven days. These are externally reported research figures, not independently audited full-history qualification. Each follow retains source attribution.

New qualifying wallets continuously join the persistent roster. Default active capacity is 12 including existing follows, further bounded by a full three-wallet polling sweep within a minute. Excess candidates remain queued; existing eligible follows stay stable, and removed/ineligible research follows stop new entries while their existing sells remain monitored. Newly activated wallets start prospectively, without copying historical buys. Public-source outages retain current follows and retry automatically. Settings expose automatic discovery, active capacity, minimum reported holding time and re-entry cooldown. All execution stays PAPER.


## Pool valuation recovery

A copied token may leave its original Pump.fun pool and trade on another pool. If the current listing omits the held pool, or explicitly reports it with no liquidity and zero five-minute volume, marks and queued PAPER exits may use a currently observed alternate with the **exact same base mint**, a recognized cash quote, positive reported liquidity, and positive five-minute volume and trade count. The highest reported liquidity among eligible alternates wins. Symbol matching, a bare alternate price, stale marks and entry prices cannot establish recovery. The original entry pool is retained as `entry_pool`, with timestamped `pool_changes`; quantity, entry cost, modeled costs and closed history are preserved. These are indicative provider prices and modeled proceeds, not guaranteed executable fills or market-impact estimates. This recovery criterion does not add an entry liquidity gate.

If any holding remains unpriced, total equity/P&L and comparison points remain null. The cards explicitly display a cash-plus-priced-holdings **subtotal** and **realized closed-trade P/L**, with the unpriced count and pending-total explanation. Missing assets are never silently valued at zero or represented as complete equity.
