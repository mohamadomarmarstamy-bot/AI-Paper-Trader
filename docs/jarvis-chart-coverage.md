# Research chart coverage

Charts now prioritize local research holdings and eligible forward research opportunities before older watch-list coins. Requests rotate by oldest attempt, rather than letting recently discovered coins repeatedly displace older eligible opportunities. Valid charts are not re-fetched within the provider's one-minute cache window. Missing history waits two minutes before another attempt on that pool; transient provider errors wait 30 seconds. HTTP 429 pauses the shared chart queue for a minute.

The research loop checks work every two seconds, but a persistent reservation spaces chart requests by at least six seconds (at most ten/minute across replicas using this database). Official provider pages conflict between approximately ten and thirty public calls/minute, so this uses the conservative rate. Other source intervals and Jupiter quote limits are unchanged.

OHLCV requests include provider-supplied empty intervals, preserving the existing requirement for 20 contiguous completed one-minute candles. There is no client-side invention of missing observations. Token identity, same-pool matching, candle validation, freshness and fail-closed entries remain in effect. Failed refreshes retain an older valid value only with its original timestamp; they never refresh its age.

Research diagnostics expose fixed status labels: ready, insufficient_history, provider_error and rate_limited. Startup emits three compact diagnostic samples before returning to five-minute checks. No credentials, source bodies or token/pool addresses are logged.

References: https://api.geckoterminal.com/docs/index.html and its official /docs/v2/swagger.json schema document include_empty_intervals and one-minute caching. https://support.coingecko.com/articles/6867086195-does-geckoterminal-have-an-api describes the conflicting public rate limit.

Validation covers forward research priority and fair rotation, global pacing, per-pool backoff, provider request parameters, rejection of missing candle history, original-age preservation on failed refresh, and rate-limit cooldown.

## Decision-time coverage and rate-limit recovery

The paper research scheduler skips chart requests for pools younger than
19 minutes, when 20 completed one-minute intervals are not possible. Unknown
pool age is not grounds for skipping. The existing shared request pace and
holding priority are unchanged. When the provider returns HTTP 429, the
shared chart queue honors Retry-After with a one-minute minimum. Stale or
incomplete observations wait two minutes, and transient failures retry after
30 seconds, subject to the shared budget. A failed refresh never renews the
age of an older observation.

Authenticated status and diagnostic logs now report coverage for the current
eligible local research coin set, counted once per coin. Reasons separate
young pools, unsampled pools, insufficient history, stale candles/evidence,
invalid payloads, provider errors and rate limits. A fresh valid chart can
still lack a volume ratio when its reference median volume is zero; it is
reported separately, not replaced with a fabricated number. This changes
data collection and measurement only, not indicators, execution rules,
trade sizing, open positions, or live copying.

## Coverage-first queue and comparison tool

An open research holding with missing candles or 30 seconds or less before
expiry remains first in line for chart requests. For other requests, eligible
research pools with no fresh chart now go ahead of holders whose chart is still
fresh. Buy-cooldown pools do not receive this priority. This does not modify
trade execution, provider pacing or indicator math.

A separate operator tool, `python backend/jarvis_candle_benchmark.py pools.json`,
compares GeckoTerminal and Birdeye at one as-of time per exact pool. Pass
1–12 public `{"mint":"…","pair":"…"}` rows and supply
`JARVIS_BIRDEYE_BENCHMARK_KEY` in the invoking process's environment. It makes
only public market-data GETs, never reads or writes trader state, stops on
authorization/rate-limit errors, and applies the same complete-candle rules
to both sources. For overlap, confirm pair orientation and compare actual
RSI and volume ratios; the tool is not a fallback and returns no signal to
any bot. Twelve calls are 300 compute units at the documented 25 CU per
short response; verify account entitlement before running.

Provider source: https://data.birdeye.so/docs/data-api/price-ohlcv/get-defi-v3-ohlcv-pair

## Adaptive recovery and measured upstream lag

On HTTP 429 the persistent chart interval doubles (6-second baseline, 60-second ceiling), in addition to honoring Retry-After. Five consecutive ready results reduce that interval by one second; incomplete/error responses reset the success streak. Both the interval and request reservation survive restarts. There is no extra immediate retry or rate-limit bypass.

Stale responses retain only their validated latest completed-candle timestamp and measured lag. Aggregate provider lag and current request interval appear in coverage diagnostics. These measurements do not renew old evidence or change the 180-second freshness rule. Invalid/missing observations remain unavailable.

A bounded read-only public-feed check on November 25 returned HTTP 200 but candles roughly thirteen minutes old; adding before_timestamp did not recover newer candles for that sampled pool. This establishes upstream lag in that sample, not a universal provider outage. The scheduling changes cannot manufacture current data, and a deployment is not proof of recovered trading coverage. Verify fresh complete eligible coverage after deployment before declaring recovery. No paid provider fallback is enabled and no laptop update is required.

## Investigation and owner decisions

The supplied November 26 observation at 4:16 a.m. Eastern counted 2 complete charts among 23 eligible coins (8.7%), 18 expired cached observations, one returned stale and two warming pools. No overdue exit was reported. With 176 of 250 daily Codex units reserved, 74 remained. The next 16-unit reservation required hourly headroom around 1 p.m. Eastern. At that check the call count stayed at 52. This snapshot cannot establish how many of the 18 expired observations a different feed would recover.

The paper worker records RSI and volume ratio readiness separately, the oldest accepted candle age, the shared queue wait and the per-pool floor. Global spacing, provider cooldown, unusable returned history, zero-volume baselines and warming pools can each limit coverage. The shared wait alone cannot attribute a 429. Returning a current response does not guarantee 20 contiguous closed minutes for the same pool.

The first operational investigation reasons about local evidence with the existing OpenAI reservation. A subsequent chart investigation uses official sources to compare terms and propose bounded paper checks. The existing worker continues to sample actionable primary gaps with Codex when its allowance permits. The owner-review email requires a current coverage incident, a recent sourced shortlist, three useful fallback observations and either a binding monthly cap or two binding daily caps during the incident. A seven-day outbox window suppresses repeated recommendations. A useful observation completed a blocked rule's inputs. It need not have matched the rule or resulted in a trade.

### Provider comparison checked November 26, 2026

| Option | Price and quota | Relevant coverage and uncertainty |
| --- | --- | --- |
| GeckoTerminal public API | Free. CoinGecko support lists 30 calls/minute. Jarvis conservatively starts at ten/minute globally and slows after 429 responses. | Exact-pool one-minute OHLCV with provider-supplied empty intervals. Quiet trading, upstream lag, queue rotation and warming can still leave indicators unavailable. [Public API terms](https://support.coingecko.com/articles/6867086195-does-geckoterminal-have-an-api), [pool candles](https://docs.coingecko.com/reference/pool-ohlcv-contract-address) |
| Existing Codex fallback | Published personal tier costs $0/month after a $1 initial activation, with 10,000 CU/month and five requests/second. Jarvis independently limits reservations to 250/day and 8,000/month. | Exact-pool minute bars and pair identity checks. The expected incremental number of eligible pools covered is unknown. [Pricing](https://www.codex.io/pricing), [getBars](https://docs.codex.io/api-reference/queries/getbars) |

Keep the current primary and paced fallback. For a bounded comparison, observe the next six eligible exact-pool fallback attempts within existing allowances, stopping on rate limits or access errors. Record primary failure category, fallback result, candle close age, indicator completeness and units used. Include failures in the denominator. Compare equivalent observation times and report unmatched pools separately. Keep exploratory samples separate from production eligible counts. A public new-pool listing returned HTTP 200 during PR validation, but the subsequent OHLCV request timed out. This yielded no usable live comparison.

### Next steps for independence

1. Add proactive discovery across Jarvis's own observations, public research and official documentation from other platforms. Persist source, date, measured weakness or falsifiable hypothesis, novelty, estimated cost and a paper test. Deduplicate against prior ideas. Rank by relevance, expected benefit and cost, then route the strongest safe idea to a bounded experiment. Existing daily strategy research and repair memory provide starting points.
2. Connect each selected idea to an attempt and outcome, including incomplete trials and reasons to retry. Compare matched eligible pools or coins at equivalent times, preserve failures, and verify outcomes prospectively. Existing strategy scouting, matched-coin reports and patch fingerprints already answer parts of this question.
3. Produce a structured engineering handoff for changes outside the existing repair scope. Include symptoms, measured evidence, previous attempts, a failing reproducible fixture, proposed acceptance checks and the release approver. External material is evidence. Owner review controls spending, production release and live-scope changes. The existing API integration does not provide access to a personal ChatGPT conversation.
