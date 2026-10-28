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
  by symbol. Latest token profiles are limited and biased, not the full market.
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

Automatic learning is opt-in. After at least 30 exploration closes under the
same exit/size policy, including at least five wins, five losses and ten mints,
one transparent liquidity/volume heuristic is frozen from the winning training
examples. No settings change occurs yet. The next 20 *newly opened* exploration
trades tagged with that candidate form its separate forward evaluation. A rule
needs at least eight matching trades, five nonmatching trades, five mints,
four matching winners, positive matching average net return and higher matching
average return than both the baseline and nonmatching trades. Only then does it
automatically set mode to liquidity/volume-only and apply its saved minimums.
It does not change exit rules, trade sizing or loss limits. Settings revision
advances so unsaved operator edits cannot overwrite the automatic change.
Failed proposals leave exploration running; another proposal waits for 20 more
closes. Pausing learning prevents promotion; changing exit/size policy invalidates
the proposal. Analysis uses the latest 1,000 archived outcomes, training at most
100, but the archive itself is not trimmed.

This is an experimental selection heuristic, not a trained AI forecast or proof
of profitability. Provider prices, omitted liquidity/sellability checks and the
limited discovery sample can make simulated outcomes unrealistic. The forward
comparison is diagnostic and does not establish statistical significance.

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
