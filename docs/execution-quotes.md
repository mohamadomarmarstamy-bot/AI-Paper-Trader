# PAPER execution estimates

Execution spread ceilings are disabled for manual buys, owner-submitted Pro Ticker buys, broad automatic entries and extended-hours exits. Fresh/valid reference requirements, ownership checks, buying power, position/order caps, regular/extended session rules, limit-price bounds and duplicate-order controls remain. Spread can still affect scanner discovery ranking; that is not an order veto. Bid/ask spreads themselves still exist and affect executable prices.

The Trade screen shows the buy estimate at ask and a separate sell estimate at bid, rather than labeling their midpoint as the current market price. Search and scanner handoffs refresh the actual execution quote. Estimates refresh every five seconds while Trade is visible, display source and market timestamp, preserve up to four price decimals, and show unavailable once the quote is older than 30 seconds. A missing quote does not prevent requesting a verified long sell.

`/quote/{symbol}` is authenticated and reads Alpaca snapshots. Its general `price` field is a fresh last trade when available, otherwise a clearly tagged fresh quote midpoint. The Trade screen explicitly uses `ask` and requires `quote_fresh`; it never treats that general field as a buy price. Bid/ask validation rejects crossed, nonpositive, nonfinite, future, untimed and stale values. Raw snapshots are coalesced for two seconds, but freshness is recomputed on each response. There is no Yahoo or previous-close fallback in this display endpoint or its Trade risk-plan preview.

`APP_QUOTE_FEED` defaults to `iex`. This is one exchange, so its prices can differ from a consolidated feed. `sip` is supported only when the account has the required entitlement; failures are surfaced, not silently replaced with another feed. This release does not add a paid subscription or change the configured feed. Market orders can fill away from either displayed estimate as the market changes. Broker-confirmed execution prices in Activity and Notifications remain the actual saved fill prices.

API contract: https://docs.alpaca.markets/us/reference/stocksnapshots-1
