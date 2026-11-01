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

After deploying the variables, the owner checks Health details and the saved buy size, then selects **Start live copying** and confirms the real-money summary. The API requires the owner session, same-origin JSON, explicit acknowledgement and current run/wallet/budget revisions. Activation checks signer identity, current source feed, buy sizing, native SOL reserve and network, then requests an unsigned SOL/USDC route to check Jupiter API access. The sample route is never signed or submitted. It does not execute a trade in the HTTP request. The source age, recorded source sells, wallet/budget revisions and pause flag are checked again immediately before broadcast under one SQLite transaction.

**Pause new buys** leaves holdings monitored for exits. A stale page revision cannot prevent a pause. Individual Sell and Sell all & pause queue exact real position IDs. Full shutdown of the server/release switch also prevents exit submissions; health reports that limitation. Changing or unlinking the wallet is blocked while active, holding coins, or reconciling an uncertain transaction. Updating buy settings pauses new entries until the owner restarts; held-position exit rules stay pinned to entry.

## Source and trade policy

The worker reads new `paper_buy` source decisions and supported source-sell events from the canonical PAPER copier's event ledger. Only future, fresh admitted buys after owner activation are candidates; rejected PAPER source buys are not silently overridden. Entry age is at most 90 seconds, source data must be current, and a source sell observed before entry prevents that buy. At the final broadcast check, a sell in the same source timestamp second cancels the buy conservatively, including fractional provider clock skew. There is one live holding per mint across all sources; repeated adds are skipped. Old rows never replay on activation. Existing holdings retain source-event processing across a restart.

Each entry captures the current paper stop, half-profit target and protective-exit cooldown. Real live exits use new Jupiter minimum-output estimates after a fee allowance, not Dexscreener chart marks or simulated prices. Stop triggers and staged half-sales run independently of browser connectivity. After a confirmed half-sale, remaining cost is allocated proportionally, the next target doubles, and a remaining-position net break-even trigger is armed. Source/manual exits take priority. An unavailable route, liquidity gap, fees, RPC outage, rejected simulation or failed transaction can delay an exit or cause losses beyond a stop trigger. There is no fill or profit guarantee.

The initial execution policy is explicit in the activation summary: 1% slippage, maximum 5% route price impact, and maximum 0.0001 SOL priority fee plus the base network fee. Initialized legacy SPL mints and a bounded subset of **Token-2022** mints are supported, without active mint/freeze authorities. Token-2022 mints may have only MetadataPointer and TokenMetadata extensions; their token accounts may have only ImmutableOwner. Unknown extensions, transfer fees/hooks, permanent delegates, pausable/non-transferable/confidential/default-state behavior, unsupported instruction layouts, pre-existing wallet holdings in the candidate coin, existing wrapped SOL balances and routes touching unrelated wallet token accounts are rejected. The server does not silently escalate slippage or fees to force a trade.

## Wallet-funded sizing

Owner settings support fixed USD per coin or a percentage of native SOL after the fee reserve. No size is inherited from the paper account. The wallet-balance preview uses confirmed SOL and an exact-mint active SOL/stablecoin pool for dollar references, cached for at most 30 seconds and expiring after 45 seconds. Existing tokens, wrapped SOL and stablecoins do not count as native funding. Missing data stays unavailable.

The actual worker rereads native funds before preparation and accounts for the reserve, priority/base fee cap and token-account rent. Fixed dollar buys that do not fit are skipped. Percentage buys apply to the remaining available native budget after those allowances. The preview is not reserved money and can differ from a live admission because fees, rent, prices, deposits and withdrawals change.

## Signing and transaction guards

The executor requests raw instructions from the fixed official Jupiter V2 `/build` endpoint. It disables upstream SOL wrapping, constructs its own canonical WSOL ATA creation/funding/sync/cleanup and compute-budget instructions, and compiles a v0 transaction locally with a fresh RPC blockhash. Top-level programs and operations are restricted: a recognized Jupiter exact-input route, expected wallet ATA creation and a recognized Jupiter route. Native transfers, wrapping, sync-native and WSOL cleanup are composed locally. Other signers, arbitrary transfers, approve operations, tips, unsupported programs and unrelated wallet token accounts are rejected. Address tables are read and validated against their on-chain owner and active layout.

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


## Token-2022 compatibility

The mint is decoded from raw confirmed account bytes, including its owning token program and complete TLV extension list. Malformed, duplicate, unknown or unsupported extensions fail closed with a specific rejection reason. Metadata does not affect transfer amounts. Mint/freeze authorities remain forbidden; existing delegated, close-authority or frozen token accounts remain rejected.

ATA derivation, validated associated-account setup, token-balance parsing and all three token guard instructions use the mint's actual program. Native SOL wrapping remains on legacy SPL. Rent allowances use the relevant token-account size (170 bytes for the supported Token-2022 immutable-owner ATA), plus legacy WSOL rent. Signed order records and resulting positions retain the token program; receipt verification checks program-tagged actual finalized token deltas. Historical legacy pending orders without a program field retain legacy behavior.

Tests run both programs' guards against local LiteSVM: exact floors, rollback, delegated authority revocation and close-authority takeover rejection. Synthetic Token-2022 preparation, ATA layout checks, buy/sell receipts, partial cost allocation and uncertain-order recovery never contact mainnet or use production keys. No historical rejected buy is replayed. Deployment expands future live token eligibility and requires owner approval.

Primary layout/behavior references:
- https://github.com/solana-program/token-2022/blob/main/interface/src/extension/mod.rs
- https://github.com/solana-program/token-2022/blob/main/program/src/processor.rs
- https://solana.com/docs/tokens/extensions/immutable-owner
- https://solana.com/docs/tokens/extensions/metadata


## Exit recovery and external wallet reductions

The first finalized-expiry observation is retained across frequent polling and restarts. An unseen signature expires only after repeated history queries, finalized block-height expiry and at least 15 seconds between first and final observations. Until then no replacement transaction is signed.

After pending bot orders resolve, compare each tracked canonical token account at finalized commitment. Mismatches pause buys and suppress signing for the affected position. A reduction is accepted only after the same quantity appears in a newer finalized slot at least 15 seconds later. Zero balance removes the stale holding from exposure; partial reductions retain proportional cost for the remaining quantity. Extra tokens are never adopted as bot-owned funds. RPC failures or old/frozen/malformed accounts do not erase a position.

External changes are not assumed to be sales. Archive the removed quantity, unreconciled cost and both observation slots with unknown proceeds. Keep an explicit accounting-review lock, incomplete P/L and no automatic restart until transaction-based accounting is reviewed. There is no automatic write-off, synthetic realized loss or inferred profit. This release does not import the owner's prior Phantom sale proceeds or alter production records during development.

Profit trims recheck a fresh quote for the actual half quantity against its proportional cost and the existing profit threshold, after the fee allowance, before signing. Stop/source/manual full exits do not get this profit-only gate. Slippage, fees, impact limits and strategy thresholds are unchanged; quotes and execution can still fail. Failed receipts expose the chain execution error; simulation rejections expose the instruction error.

The dashboard's realized P/L card uses finalized realized ledger P/L only, rather than realized plus unrealized estimates. It shows accounting review when external proceeds are unknown. No PAPER logic is changed.

## Owner-selected paper-only wallets

Solkrow and Kay The Doc remain actively followed by the main paper copier but are excluded from new live entries. The live source policy also retains Jijo, Tech and Kadenox's paper-only restriction. Live status exposes `paper_active` and `paper_only`, and its active count excludes these sources. Signal admission and first broadcast both enforce the restriction, including recovered signed orders that have not been sent. Already-broadcast transactions are reconciled normally, and existing holdings retain normal sell/protective exit monitoring. No paper roster, simulated cash, strategy thresholds, historical records or live buy sizing is edited.
