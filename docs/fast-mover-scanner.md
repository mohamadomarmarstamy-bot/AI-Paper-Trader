# Fast-mover discovery v1

The main PAPER scanner combines Alpaca gainers/losers, top 100 most-actives by volume, top 100 by trade count, and a rotating 300-symbol slice of the active, tradable equity list. Deduplication bounds the snapshot pool to 600 symbols per refresh. Up to 350 candidates reach daily analysis, reserving 50 places for exploration. Rotation advances on a completed discovery refresh, not on cached reads. The asset master refreshes every 15 minutes; discovery normally refreshes every minute.

Ranking favors a fresh positive session move of at least 3%, a price from $0.25 to $30, at least $250,000 of selected-feed session dollar volume, a spread no greater than 2%, and price at or above session VWAP. Extreme moves above 80% receive a lower discovery score than the same otherwise equivalent move below that threshold. These are discovery preferences, not proven return predictors. Low price is not proof of small market cap or low float; neither is verified here. Corporate actions can distort prior-close changes.

Snapshots explicitly use IEX. Trade and quote timestamps must be within 90 seconds, daily data must belong to the current Eastern trading date, and the prior session must be earlier and within seven days. Missing, delayed, crossed or invalid data does not receive a live ranking boost. Quotes and volume cover the selected exchange, not the consolidated market. Session dollar volume is volume times session VWAP; it is not time-adjusted relative volume. Screeners themselves use Alpaca's published market-wide lists.

Technical BUY/HOLD/SELL classification, entry score/confidence gates, independent verification, position sizing, daily/account risk limits, exits, and owner-selected Pro Ticker execution are unchanged. Fresh discovery affects ordering within BUY/HOLD groups; it never upgrades HOLD to BUY or changes technical scores. At most 50 results per signal category are retained. The scanner endpoint supports up to 150 results; the UI defaults to Fast movers sorting and still offers score sorting and filters.

Daily analyses can be reused for up to five minutes. Live snapshots are refreshed again after the daily pass, avoiding a live boost based on data that aged out during downloads. Cache copies also strip expired priority boosts. Each daily-history request has a 12-second timeout, with a 45-second admission budget between batches; the last batch and discovery calls can take additional time. Remaining histories are reported as deferred. Source data, minimum 55-session technical history and existing volume filters remain required; this release does not invent indicators for recent IPOs. The first scan can take longer than subsequent cached scans.

Coverage reports distinguish tradable-universe size, screened symbols, rotating symbols, requested daily histories, reused daily analyses, valid candidates, fresh snapshots, deferred work and stale/partial results. Coverage describes the available pass, not a claim to have analyzed the entire market or found profitable stocks.

Primary API contracts:
- https://docs.alpaca.markets/us/reference/mostactives-1
- https://docs.alpaca.markets/us/reference/movers-1
- https://docs.alpaca.markets/us/reference/stocksnapshots-1
