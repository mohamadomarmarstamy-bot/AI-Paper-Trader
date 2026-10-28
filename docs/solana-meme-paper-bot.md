# Solana Meme Coins — paper bot v1

Separate page: `/meme-coins`, linked from the stock sidebar. Its virtual USD
portfolio starts at $1,000 and never shares Alpaca balances, orders or settings.
No wallet, signing key, seed phrase, transaction builder, or swap submission
exists in this implementation. No live-trading control is exposed.

## Operation

The owner can save up to 20 full mint addresses and opt into latest token
profile discovery. New entries are disabled until **Start paper bot** is clicked.
The server checks every 10 seconds while enabled or paper positions remain open,
including when the page is closed. **Pause new buys** keeps existing simulated
positions under exit management. **Scan now** performs a read-only observation
and applies the paper entry rules only if entries were already enabled.

Configuration, positions, observations, entry-time exit rules, cooldowns, UTC-day
baseline and the latest 200 closed simulations persist in a dedicated SQLite
table. A database lease prevents overlapping cycles. Revision-checked commits
discard a simulated cycle if configuration changes while data is being fetched.
No paper account reset control is exposed. Starts, pauses and settings writes
require an authenticated owner session and same-origin JSON requests.

## Fixed experimental selection and entry rules

- Solana base mint and the same pool across observations; do not identify a token
  by symbol. Profiles and fixed meme-topic searches are limited and biased, not the full market. Search matches do not verify meme status or safety.
  Paid boosts are not used for ranking or approval.
- At least $100,000 reported liquidity, $20,000 five-minute volume, 40 five-minute
  buy/sell transactions, buy activity at least 1.2 times sell activity, and pair
  age from 30 minutes to 30 days.
- Five-minute change between +2% and +20%; a second same-pool observation 45–180
  seconds later must show +0.5% to +8% continuation by default. The second-observation check is configurable in Settings.
- Finalized on-chain mint account: initialized legacy SPL mint, revoked mint and
  freeze authorities. Token-2022 and other programs are currently unsupported.
- Largest token account at most 35% of supply; ten largest at most 70%. These are
  accounts, not unique people, and pool vaults are included. This conservative
  screen has false positives and cannot detect distributed insider ownership.
- Default $50/trade, three simultaneous positions, available virtual cash, and
  a $50 daily marked-equity loss brake. The first complete observation after UTC
  midnight establishes that day's baseline. Missing marks block new entries.
- Only three on-chain candidate checks and 20 observed mints per cycle.
  Discovery holds each batch for the configured confirmation wait plus two scans to support confirmation, then rotates;
  open positions and the saved watchlist have priority.

These thresholds are deliberately fixed hypotheses, not optimized or proven
profitable. Model performance must be evaluated prospectively across conditions.

## Exits and cost model

Defaults: 8% net modeled stop, 20% net modeled take profit, trailing protection
armed at 12% gain with a six-percentage-point giveback, maximum hold 30 minutes.
A 50% reported-liquidity decline also triggers a simulated exit. Entry-time exit
rules remain fixed when settings change. Re-entry cooldown is one hour.

Entry quantity = notional / (observed price × 1.01). Entry debits notional plus
$0.10. Exit proceeds = quantity × observed price × 0.99 minus $0.10, floored at
zero. This approximates friction; it does not establish actual execution fees,
slippage, price impact, rent, priority fees, taxes or sellability. Stops are
sampled, not guaranteed. Missing or changed-pool observations leave exits
unresolved and equity unavailable; no invented zero-cost exit occurs.

## Provider boundaries and research

- DEX Screener documented token-profile and token-pair endpoints supply public
  discovery and pair observations. The API exposes aggregated liquidity,
  activity and price fields; those are not executable quotes or independent
  evidence of organic activity. The UI distinguishes observation time from an
  unavailable underlying market timestamp:
  https://docs.dexscreener.com/api/reference
- Solana mint/freeze authority roles control specific permissions. Revocation
  does not prove liquidity lock, fair distribution, authenticity or safety:
  https://solana.com/docs/tokens/basics/set-authority
- Read-only `getAccountInfo` and `getTokenLargestAccounts` are the only RPC
  methods allowed by the adapter. The latter returns token accounts, not unique
  holders: https://solana.com/docs/rpc/http/gettokenlargestaccounts
- Jupiter quotes and transaction execution are distinct steps; the older Metis
  Swap V1 documentation now points to Swap V2. No legacy quote endpoint, API key
  registration, subscription, or swap integration is silently added:
  https://developers.jup.ag/docs/swap/v1/get-quote

The paper provider uses fixed public DEX Screener and Solana mainnet hosts, with
bounded batches and timeouts. Failures/rate limits block new entries. Before any
future funded bot, validate actual buy and sell routes, transaction identities,
output limits, Token-2022 behavior, confirmation/recovery, custody and emergency
controls, and assess real costs and prospective results. Paper gains cannot
establish that a route can execute or that a token is safe to buy.

## Compact interface

Controls, portfolio totals and compact candidate/position/trade rows stay visible.
Settings, trading rules and data notes are collapsed by default. Full mint
addresses and longer explanations are available in per-row Details, whose open
state survives refreshes. Watching means confirmation is pending; Skipped means
a rule failed; No data names the missing provider evidence. Thresholds are unchanged.

Liquidity and five-minute volume minimums are editable in Settings (USD, 0–1 billion; zero disables only that minimum). Defaults remain $100,000 and $20,000. Changes apply to new entries, not open-position exit rules. Missing metrics still prevent entry. Existing saved configurations receive these defaults without resetting balances, positions, or other settings.

Settings use a dedicated revision, independent of scan writes, and remain in SQLite across refreshes/restarts. Concurrent settings edits still conflict. The form displays save success or errors beside Save. Missing batch data triggers at most four alternate-pool lookups per cycle, then incomplete discovered mints are skipped for five minutes. Positions and watchlist mints remain monitored; held positions never switch pools. Missing values are never fabricated.

The meme workspace has Home (/meme-coins), Trades (/meme-coins/trades), Scanner (/meme-coins/scanner), and Settings (/meme-coins/settings). Every route requires the owner session. Home shows cash, current marked equity, P/L since the original $1,000, open holdings and separately scoped realized/open P/L. Equity history begins on rollout, stores at most 1,440 minute-spaced observations, and leaves unavailable observations as gaps. The trades queue shows the existing automatic confirmation window using server timestamps; timer completion does not authorize or guarantee a buy. Scanner counts/filters describe the latest scan only, with incomplete discovery backoff shown separately. Navigation does not write settings or trigger orders.

Second-price confirmation is configurable: enabled by default, wait 10–180 whole seconds (default 45), minimum rise 0–20% (default 0.5), maximum rise 0.1–100% (default 8), with minimum strictly below maximum. Off skips only this wait/continuation check. All other entry and exit rules remain active. The baseline stays fixed until expiry (at least 180 seconds, or wait + 60) instead of restarting after each failed attempt. Discovery retains each batch long enough for the configured wait plus two scans. The queue timer uses the saved wait. Settings are persisted; existing configurations keep confirmation enabled with original thresholds.

### PAPER exploration and prospective learning

Settings offers `filtered` (existing full checks), `explore` (no entry filters),
and `learned` (liquidity/volume minimums only). Exploration/learned modes bypass
momentum, pool-age, activity, continuation, mint-authority and concentration
checks; neither mode verifies sellability. They require a valid Solana mint and
pool identity plus a positive finite observed price. Other metrics may be null,
never zero-filled. Entry costs still use the modeled 1% + $0.10 per side.
Cash, daily loss brake, position cap, pause, re-entry cooldown and existing exit
rules remain enforced. Only PAPER simulations exist; no wallet or swap is added.

Every new closed trade is appended atomically with state updates to
`solana_meme_learning_outcomes`, independently of the latest-200 closed list.
The journal preserves winners, losers, entry features, entry mode, frozen exit
policy, modeled net result, excursion and a plain-language note. All records
are retained; the UI shows the latest 20 and lifetime counts. Older trades
without captured entry features are not backfilled or invented.

Automatic learning now uses repeating, non-overlapping batches of exactly 50
archived closed trades. A persisted SQLite row cursor survives restarts and
prevents duplicate evaluation. Missing-feature outcomes still count toward the
50 but are excluded from threshold comparisons. At least 40 comparable outcomes
under the same exit/size policy and five mints are required. The first 30 usable
outcomes select one liquidity/volume grid candidate; later outcomes in the batch
are held out. A candidate requires ten training matches, five held-out matches,
three matching mints and positive held-out average return above the baseline.
This is a retrospective held-out diagnostic, not a new prospective validation
or proof of profitability. Insufficient or unimproved evidence records no change.
Only entry mode and liquidity/volume minimums may change; size, funding, exits,
position cap and loss limits are unchanged. Every evaluation is permanently
archived, and the UI shows the latest 20.

Every tenth actual automatic settings change queues a ten-version review.
Versions are stamped at position entry. Each version needs at least ten closed
outcomes and three mints under the same exit/size policy; otherwise the review
waits for evidence. The best observed average net return may restore an earlier
version's thresholds if positive. Reviews compare different market periods and
are not causal proof that one version is superior. A resulting settings change
also counts toward the next ten-change group. Pausing automatic learning stops
evaluations without deleting their cursor or trade history.

Actual changes enqueue an email atomically with the state/evaluation update,
using the app's existing RESEND_API_KEY and HEALTH_ALERT_EMAIL_TO/FROM destination.
The separate learning email switch controls sending. Delivery retries use a
stable provider idempotency key and frozen payload; accepted messages are not
resent. Retries stop for operator attention after 23 hours of uncertain delivery,
inside Resend's 24-hour idempotency window. Provider acceptance is shown as
accepted, not inbox-delivered. Missing configuration leaves mail queued; failures
never undo settings changes. No email is sent for an unchanged evaluation.

### Larger virtual portfolios and chart inspection

The position cap accepts whole numbers from 1 to 1,000. Discovery remains a
20-token batch; held positions no longer consume all discovery slots. Every
holding is monitored in bounded 20-mint requests with at most eight concurrent
requests. Missing observations remain unavailable and still block new entries;
no capacity increase guarantees a fill or exactly 10-second completion at scale.

Virtual capital is a saved cumulative funding amount (initially $1,000 for
existing accounts). Increasing it adds only the difference to cash and the
current day's equity baseline. Re-saving the same amount cannot credit twice.
Existing positions, trade notes and P/L are preserved. Funding never counts as
profit; reductions/withdrawals are not supported by this control. The owner
requested $100,000 virtual capital and $50 buys; trade size is independent of the
position cap. Existing daily loss and exit limits still apply.

The home chart supports pointer dragging, hover, touch and left/right arrow
inspection of actual saved snapshots, with exact value/time readouts and gaps
for missing marks. Portfolio-value and funding-adjusted P/L views are available
for one hour or 24 hours. Historical points capture their cumulative funding;
legacy points retain the original $1,000 basis. Funding changes are identified
in the tooltip rather than presented as trading gains.

History has its own authenticated route (/meme-coins/history), with completed-trade cards colored by net P/L (green wins, red losses, neutral flat). The journal/evaluation history moved there too. The home control toggles Start/Stop from the saved enabled flag; enabled is red Stop paper bot. Stop pauses new entries but preserves monitoring/exits for existing positions.

History includes a separate Settings changes panel showing actual old/new values, actor, timestamp, automatic-evaluation reason and email status. Existing automatic changes are read from their permanent evaluation archive; manual changes are archived atomically from this release onward. No-op saves and no-change evaluations are excluded. The panel shows the latest 50 actual changes independently of the latest 20 evaluations. Manual audit entries do not enqueue automatic-change emails. Broker watchdog emails remain separate health alerts.

Manual PAPER exits are available on each holding in Home/Trades, plus Sell all holdings on Trades. Sell all confirms and pauses new entries atomically so closed holdings are not immediately replaced. Requests carry exact position identities, never prices or amounts. Manual and scheduled cycles share the same lease and state CAS; stale identities cannot close a re-entry and retries cannot credit a closed trade again. The server fetches a fresh positive observation of the original pool, applies the same simulated exit costs, archives the close, and reports missing-price positions as unresolved. Unrelated holdings are left alone. Manual outcomes remain in History and count as closed trades for the 50-close clock, but are excluded from automatic-policy threshold/version comparisons because the owner chose their exit time. No wallet or real swap is submitted.

Scanner controls: coins per discovery scan (1–200, default 20) and cycle interval in whole seconds (5–60, default 10) are saved and audited. The feed may supply fewer coins. Protocol requests stay in 20-mint chunks; all held positions remain monitored independently. Small discovery caps rotate the watchlist rather than permanently starving its later entries, and batch retention adjusts to preserve second-price confirmation. The worker measures start-to-start intervals, never overlaps scans, and wakes when settings are saved. For large portfolios, a calculated request-budget minimum may lengthen the requested interval; the scanner displays this minimum. Local rolling limits cap DEX calls at 240/minute and profile requests at 50/minute. Network/provider limits may delay a cycle or leave marks unavailable; prices are not invented. UI refresh polling remains separate from server scans.

Discovery now merges latest and recently updated profiles with sixteen fixed meme-topic DEX Screener searches. It deduplicates Solana base mints, caches only identifiers for 60 seconds, and retains up to 1,000 discovered identifiers for at most one hour. Each entry/exit cycle still fetches current pool evidence in 20-mint requests. Individual discovery failures retain usable cached identifiers; empty discovery reports unavailable. No paid boost ranking is used. Scanner shows actual/requested counts and the available universe so a feed shortfall is explicit. The conservative request-budget schedule includes discovery refresh calls.

Numeric settings no longer have arbitrary upper bounds. All must be finite and nonnegative; percentages are limited to 100 and coin counts must be whole numbers. Minimum confirmation rise cannot exceed maximum. A zero scan count selects no discovery candidates, zero position count blocks entries, and zero trade amount blocks entries without creating zero-sized positions. Zero timing requests are bounded by the provider-budget schedule; network limits and available discovery supply still apply. Virtual funding remains increase-only to preserve accounting. Existing settings are not changed by this release.

### Missing holding prices

Held positions receive a direct original-pool lookup when the token listing omits their pool, before the existing alternate token-list lookup. Both share the four-request recovery allowance; wrong chain/base mint/pool or invalid prices cannot mark or close a holding.

When current marks are missing, current equity/P&L and equity-history observations remain unavailable to trading/accounting and the copy comparison. A separate `valuation.indicative_equity_usd` combines current cash with each holding's last valid saved mark only when all holdings have one. The dashboard labels it with `~` and an estimate note, shows the oldest mark time, and explains why new entries are waiting. This is an indicative view, not a fresh portfolio observation or an executable exit price; it never enters the trading engine or equity history.
