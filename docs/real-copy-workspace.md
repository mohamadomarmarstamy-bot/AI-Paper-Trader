# Real Copy Trade

The stock dashboard sidebar opens `/#real-copy`, an isolated Solana real-money copier. Stocks remain PAPER. The real account has its own wallet, orders, holdings, actual SOL P/L, buy settings and worker. PAPER funding, historical fills and positions never become real account assets.

## Owner setup and activation

The software is installed with two independent locks: `REAL_COPY_EXECUTION_ALLOWED` defaults off, and the persisted real run defaults paused. A deploy, Phantom link, settings save or key configuration never activates trading.

The owner configures Railway service Variables directly:

- `REAL_COPY_SIGNER_SECRET`: exported **64-byte** dedicated trading-wallet key, in base58 or a JSON byte array. It must match the ownership-verified Phantom public address. Do not enter a recovery phrase, send the key through chat, put it in source files, or paste it into this application's pages. There is no web API accepting it. The key stays in server configuration and is loaded only in memory; it is never returned, logged, or persisted in the trading database.
- `JUPITER_API_KEY`: Jupiter Portal API key for the current Swap V2 `/build` API.
- `REAL_COPY_EXECUTION_ALLOWED=true`: allows owner activation; it does not activate the bot.
- Optional `SOLANA_REAL_RPC_URL`: dedicated HTTPS mainnet RPC. Without it the executor uses `https://api.mainnet-beta.solana.com`. A mainnet genesis check is required before signing. Public RPC has no availability guarantee. Source-wallet observation still uses the PAPER copier's configured data sources and limitations.

**The server key can spend that wallet's funds. These are application checks, not a wallet-level delegation limit. Use a wallet containing only the intended trading funds.** A connected Phantom browser is not an unattended signer. A wallet-link message proves ownership and grants no transaction authority.

After deploying the variables, the owner checks Health details and the saved buy size, then selects **Start live copying** and confirms the real-money summary. The API requires the owner session, same-origin JSON, explicit acknowledgement and current run/wallet/budget revisions. Activation checks signer identity, swap access configuration, current source feed, buy sizing, native SOL reserve and network. It does not execute a trade in the HTTP request.

**Pause new buys** leaves holdings monitored for exits. A stale page revision cannot prevent a pause. Individual Sell and Sell all & pause queue exact real position IDs. Full shutdown of the server/release switch also prevents exit submissions; health reports that limitation. Changing or unlinking the wallet is blocked while active, holding coins, or reconciling an uncertain transaction. Updating buy settings pauses new entries until the owner restarts; held-position exit rules stay pinned to entry.

## Source and trade policy

The worker reads new `paper_buy` source decisions and supported source-sell events from the canonical PAPER copier's event ledger. Only future, fresh admitted buys after owner activation are candidates; rejected PAPER source buys are not silently overridden. Entry age is at most 90 seconds, source data must be current, and a source sell observed before entry prevents that buy. There is one live holding per mint across all sources; repeated adds are skipped. Old rows never replay on activation. Existing holdings retain source-event processing across a restart.

Each entry captures the current paper stop, half-profit target and protective-exit cooldown. Real live exits use new Jupiter minimum-output estimates after a fee allowance, not Dexscreener chart marks or simulated prices. Stop triggers and staged half-sales run independently of browser connectivity. After a confirmed half-sale, remaining cost is allocated proportionally, the next target doubles, and a remaining-position net break-even trigger is armed. Source/manual exits take priority. An unavailable route, liquidity gap, fees, RPC outage, rejected simulation or failed transaction can delay an exit or cause losses beyond a stop trigger. There is no fill or profit guarantee.

The initial execution policy is explicit in the activation summary: 1% slippage, maximum 5% route price impact, and maximum 0.0001 SOL priority fee plus the base network fee. Only initialized **legacy SPL** mints without active mint/freeze authorities are supported. Token-2022, unsupported instruction layouts, pre-existing wallet holdings in the candidate coin, existing wrapped SOL balances and routes touching unrelated wallet token accounts are rejected. The server does not silently escalate slippage or fees to force a trade.

## Wallet-funded sizing

Owner settings support fixed USD per coin or a percentage of native SOL after the fee reserve. No size is inherited from the paper account. The wallet-balance preview uses confirmed SOL and an exact-mint active SOL/stablecoin pool for dollar references, cached for at most 30 seconds and expiring after 45 seconds. Existing tokens, wrapped SOL and stablecoins do not count as native funding. Missing data stays unavailable.

The actual worker rereads native funds before preparation and accounts for the reserve, priority/base fee cap and token-account rent. Fixed dollar buys that do not fit are skipped. Percentage buys apply to the remaining available native budget after those allowances. The preview is not reserved money and can differ from a live admission because fees, rent, prices, deposits and withdrawals change.

## Signing and transaction guards

The executor requests raw instructions from the fixed official Jupiter V2 `/build` endpoint. It constructs its own compute-budget instructions and compiles a v0 transaction locally with a fresh RPC blockhash. Top-level programs and operations are restricted: a recognized Jupiter exact-input route, expected wallet ATA creation, the exact SOL wrapping transfer, sync-native and WSOL cleanup. Other signers, arbitrary transfers, approve operations, tips, unsupported programs and unrelated wallet token accounts are rejected. Address tables are read and validated against their on-chain owner and active layout.

After the swap and cleanup, the executor appends independent SPL/native **post-balance floors**. SPL self-transfer checks the output mint, decimals, authority and minimum token balance before its no-op branch; System transfer to self checks the minimum SOL balance. It also revokes token delegation and clears close authority as owner. A failed floor or authority check atomically reverts the swap. A fresh preflight simulation must pass before the authorization callback and signing. These balance floors supplement the route's slippage protections; use the dedicated wallet without concurrent manual coin trades. They are not a wallet-level restriction on the server key.

Signed bytes, signature and block-height expiry are committed before broadcast. One unresolved transaction is allowed at a time. Timeouts do not create replacement orders: the worker polls the exact signature and may rebroadcast only the same bytes. Interrupted unsigned preparation is abandoned. A signed buy never sent is canceled if the owner pauses. Expiry requires finalized block height beyond validity plus two separated signature-history checks; a confirmed-but-not-finalized signature cannot be considered expired.

Positions and P/L are posted only from the matching **finalized** transaction's actual token/native balance changes. A failed finalized transaction contributes its actual network fee, never a fictional fill. Order completion and position accounting commit in one SQLite transaction. The finality wait and serialization reduce throughput and delay additional exits while a prior transaction is unresolved. This is deliberate duplicate-spend protection, not immediate execution.

Unexpected receipt bytes/deltas or unavailable history remain unresolved and block new orders. They are not manually marked filled by the app; inspect the transaction and wallet before recovery. No real on-chain trade is used by automated verification.

## Phantom sessions and health

Ownership linking uses an origin/revision-bound five-minute Ed25519 nonce, with replay/expiry/account-change protection. Trusted browser sessions restore with `onlyIfTrusted` and do not sign messages or transactions on refresh. Explicit disconnect/unlink prevents silent restoration. The profile restores connection status without overwriting saved or edited wallet fields.

Health distinguishes page connection, browser Phantom session, wallet ownership/funding, source feed, live worker, server signer, swap API, pending confirmations and missing live exit quotes. A healthy paper feed or linked wallet cannot imply enabled real execution. Page failures turn red immediately; successful responses expire locally (amber after 30 seconds, red after 60). Live balance and source/worker freshness also expire between polls.

## Validation

Backend verification uses synthetic keys, mocked Jupiter/RPC responses, an isolated SQLite ledger and the local Solana LiteSVM runtime. It checks atomic guard rollback, token authority protection, local signing, finalized receipts, duplicate suppression, timeout recovery, partial exits/cost allocation, fees, pause/settings behavior and wallet-binding restrictions. Desktop/mobile browser scenarios use synthetic service responses and test explicit activation confirmation, decline, pause, manual exit requests, funding controls, stale data and Phantom restoration. None demonstrates real liquidity, production API entitlements or mainnet trade execution.

Primary implementation references:
- https://developers.jup.ag/docs/api-reference/swap/build
- https://kevinheavey.github.io/solders/api_reference/transaction.html
- https://github.com/solana-program/token/blob/main/program/src/processor.rs
- https://docs.phantom.com/solana/establishing-a-connection
