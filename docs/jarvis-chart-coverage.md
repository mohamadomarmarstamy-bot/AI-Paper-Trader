# Research chart coverage

Charts now prioritize local research holdings and eligible forward research opportunities before older watch-list coins. Requests rotate by oldest attempt, rather than letting recently discovered coins repeatedly displace older eligible opportunities. Valid charts are not re-fetched within the provider's one-minute cache window. Missing history waits two minutes before another attempt on that pool; transient provider errors wait 30 seconds. HTTP 429 pauses the shared chart queue for a minute.

The research loop checks work every two seconds, but a persistent reservation spaces chart requests by at least six seconds (at most ten/minute across replicas using this database). Official provider pages conflict between approximately ten and thirty public calls/minute, so this uses the conservative rate. Other source intervals and Jupiter quote limits are unchanged.

OHLCV requests include provider-supplied empty intervals, preserving the existing requirement for 20 contiguous completed one-minute candles. There is no client-side invention of missing observations. Token identity, same-pool matching, candle validation, freshness and fail-closed entries remain in effect. Failed refreshes retain an older valid value only with its original timestamp; they never refresh its age.

Research diagnostics expose fixed status labels: ready, insufficient_history, provider_error and rate_limited. Startup emits three compact diagnostic samples before returning to five-minute checks. No credentials, source bodies or token/pool addresses are logged.

References: https://api.geckoterminal.com/docs/index.html and its official /docs/v2/swagger.json schema document include_empty_intervals and one-minute caching. https://support.coingecko.com/articles/6867086195-does-geckoterminal-have-an-api describes the conflicting public rate limit.

Validation covers forward research priority and fair rotation, global pacing, per-pool backoff, provider request parameters, rejection of missing candle history, original-age preservation on failed refresh, and rate-limit cooldown.
