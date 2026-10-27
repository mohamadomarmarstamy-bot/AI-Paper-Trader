# Owner-submitted PAPER buys with scanner exits

`PRO_TICKER_MANUAL_SCANNER_ENABLED=true` enables a separate, explicit owner workflow in the original Alpaca PAPER account. The Research Lab button reads **Buy ~$2,000 PAPER & research**. Submitting it saves research and requests a buy even when the technical grade is unavailable or HOLD. Research-only endpoints continue to create no orders. No model training is enabled.

Quantity is `floor(2000 / fresh ask)`, with whole shares only. For a $45.50 ask, the initial request is 43 shares ($1,956.50 estimated). Existing account risk limits may reduce that quantity or block the purchase. This is a market order, so the actual fill price and cost can differ. A share above $2,000 is blocked.

The regular-session workflow requires an authenticated owner, matching Origin, active PAPER account, recent scanner-worker heartbeat, fresh execution quote, regular open session, buying power, and existing position/order risk checks. It rejects an existing position in the same symbol. With premarket disabled it does not queue closed-market buys. Start a new review after opening to make a new request. Repeating the same request ID recovers the existing order and never creates another buy. An unresolved order blocks further new entries until reconciled.

The broker receives the existing native stop-loss/take-profit bracket. The separate 15-second worker runs the existing scanner analysis for each owned manual position, including symbols outside the scanner's displayed top results. It may exit on SELL with score <=40 and confidence >=80, using the existing exit settings. Native protective orders can exit sooner. This is a daily-indicator scanner, not a tick-by-tick intraday strategy. All old broad automatic-buy flags and the original automatic-entry pause remain unchanged.

Only the entry's verified remaining whole shares and owned bracket orders can be closed/canceled. Mixed holdings or foreign orders block the action. If an entry only partially fills, its remainder is canceled and the verified filled quantity is flattened for safety once cancellation is confirmed. If it completes during cancellation, its native bracket is retained. Terminal priced fills are posted idempotently to the original trade book under `owner_manual_scanner_v1`; native partial exits and later scanner exits are combined by actual prices and quantities.

Durable dispatch markers and stable buy/sell client IDs precede broker requests. Lost responses are recovered by reading the same client ID; no automatic POST replay occurs. Broker/network errors, unconfirmed cancellations, or mismatched ownership produce `attention`/uncertain state. Inspect broker fills and protective orders before resolving; the app cannot guarantee protection during broker failures. A partial entry that cannot be safely canceled or exited requires operator attention. A canceled partial entry without a broker completion timestamp is still flattened for safety, but exact journal accounting remains in attention until its actual fill evidence is reconciled; no timestamp is invented.

## Rollout and rollback

The flag defaults off. The status route `/auto-trader/pro-ticker/manual-entry-status` requires an owner session and reports mode, target, and heartbeat without broker reads. Enabled UI is disabled until the worker has a current matching-account heartbeat. Refresh saved reviews to inspect order state, filled quantity, and broker status. Existing unavailable research records are not recalculated.

Staging remains research-only and has no order worker. Tests use synthetic broker fixtures; no arbitrary deployment test order is authorized. Before rollout run the complete backend suite, DOM tests, and desktop/mobile browser fixtures. Preserve the existing database backup-validation startup guard. Enable only the new flag; do not enable broad automation or dedicated research execution flags.

Turning the new flag off stops both new manual buys and its scanner worker. Existing broker-native protective orders remain at Alpaca. Inspect/manage open positions before disabling; rollback does not cancel orders or liquidate positions automatically. Tables are additive and old trade-book rows are preserved.

## Premarket extension

`PRO_TICKER_PREMARKET_ENABLED=true`, together with the existing manual-scanner flag, enables explicitly submitted premarket PAPER entries and owned-position premarket exits. It does not enable broad scanner buying, after-hours entry, overnight entry, or live trading.

The broker clock and same-day calendar must agree on a normal trading day and regular opening. The supported premarket window is 4:00–9:30 a.m. America/New_York, including DST handling; new entries stop 75 seconds before opening. Holidays, weekends, stale/mismatched clocks, unsupported calendar openings and missing fresh bid/ask data block dispatch. The default IEX feed starts its premarket session at 8 a.m.; it cannot supply fresh 4–8 a.m. quotes. `PRO_TICKER_MANUAL_QUOTE_FEED=sip` supports an existing SIP entitlement, without purchasing a subscription or falling back to delayed data if denied.

Premarket buys are simple extended-hours DAY limit orders. The buy limit is the fresh ask rounded up to a legal price increment; size is floor($2,000 / that limit), subject to existing risk caps. The limit is saved before transport and is never chased upward. The worker requests cancellation of an unfilled buy after 60 seconds, and cancels partial entry remainders immediately. It confirms final broker quantity before attaching protection. Cancellation timing depends on the worker and broker being available: a DAY order can still fill later if the app is offline or cancellation is unconfirmed. No replacement buy is automatically posted.

After a terminal priced entry fill, a separately identified GTC OCO is attached for regular-hours stop-loss/take-profit protection using the saved entry-time percentages and actual fill price. Its durable marker and stable client ID prevent duplicate protective submissions after a restart or lost acknowledgement. Premarket stops/targets are app-monitored: native OCO stops do **not** execute in premarket. The worker can cancel only owned protective orders and submit an extended-hours DAY limit sell on a stop, target or existing scanner SELL signal. Regular-origin manual entries also support these premarket exits; regular-hour market/bracket entry behavior remains unchanged.

Premarket sell limits use the current bid with a 0.1% cushion, rounded down to a legal increment. Liquidity and fills are not guaranteed. A pending sell remains `closing`; it is not recorded as realized profit or automatically duplicated/repriced. DAY extended-hours sell limits may remain executable later in the trading day, including after-hours, until filled, canceled or expired. Inspect pending/partial/rejected exits and broker protection when attention is required. The worker checks approximately every 15 seconds; app downtime, stale feeds or broker outages can leave premarket positions without an active stop. This is visible next to the submission button and in entry details.

Turning the premarket flag off blocks new premarket dispatches while preserving regular-hours management and existing native protection. Turning the entire manual-scanner flag off stops that worker too. Review broker positions and outstanding orders before disabling either mode. All added columns default existing entries to `regular`; original entry quantities, broker identities, orders and historical trade-book values are preserved.

Broker requirements: https://docs.alpaca.markets/us/docs/orders-at-alpaca
IEX session coverage: https://www.iex.io/resources/trading/trading-hours-holidays

## Per-stock amount and protection settings

Pro Ticker → Settings & Health → Manual PAPER order settings provides dollars per stock, take-profit percent and stop-loss percent. Values are separate from main-app settings. Until first saved, the amount defaults to $2,000 and protection inherits current main-app percentages. Once saved, these Pro Ticker values remain independent. Amount supports $1–$50,000 in cents; stop loss 0.1–25%; take profit 0.1–100%. Existing buying-power and order/position caps still apply and may reduce or block sizing. Regular market orders can fill at a different price; the dollar target is approximate.

Each new submission freezes its amount and percentages in its durable entry record. Whole-share sizing and native regular-session bracket prices use that snapshot; premarket OCO protection uses the same saved percentages. Editing preferences never changes existing positions, pending orders or an unchanged request-ID retry. Revision checks reject stale saves and stale buy forms; refresh before confirming the new amount. No order is placed by saving settings.

The connection button verifies the active account mode. When manual execution is enabled it checks the original PAPER account and matching-account worker heartbeat, without requiring an unrelated dedicated research account. The dedicated-account check remains available for dedicated mode. The research ledger's reconciliation status is distinct from manual account/worker readiness; no ledger reconciliation is fabricated by an account check.


## Owner selection and exit spreads

Owner-submitted symbols attempt entry independently of technical grades and scanner entry coverage. Missing daily analysis does not veto the owner's PAPER buy. Scanner analysis remains in use for subsequent owned exits, alongside saved stop-loss/take-profit protection.

The entry spread ceiling still applies to broad automatic buys. It does not apply to owner-submitted Pro Ticker buys or to verified long-position sells. Buying power, tradability, position/order limits, fresh execution references, supported sessions, broker acceptance and duplicate-dispatch protections still apply. Wide spreads can make executable exit proceeds lower than a displayed latest-trade gain. Blocked entries report the specific risk reason. Saved blocked submissions are not replayed; a fresh review is required after correcting the blocker.
