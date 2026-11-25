# Codex exact-pool USD feed

Server-side only. JARVIS_CODEX_API_KEY is private; never returned, logged, or copied to a laptop. Default JARVIS_CODEX_MODE=trial. Trial samples at most three distinct eligible pools and six requests for the lifetime of this database. Trial values never enter research evidence or trading. Mode fallback must be explicitly enabled after examining results; off disables access.

Metadata is requested for the exact pool on Solana network 1399811149. Verify returned pair ID/network and both token addresses; explicitly select the requested token0/token1. Candle queries require symbolType POOL, resolution 1, USD currency and at most 30 bars. USD volume is the sum of documented buyVolume and sellVolume. Validate returned pool identity, aligned timestamps, duplicates, OHLC bounds, nonnegative volumes and equal array lengths. Use only candles closed at least five seconds earlier. Preserve existing twenty-bar contiguous history and 180-second freshness limits. No guessed bars, token-to-pool substitution, native-price conversion or stale-data bypass.

Only primary chart failures within the last 180 seconds for recent discovery candidates can invoke Codex. A separate selector runs at most once per minute: retain active slots with cash and position capacity, exclude previously traded coins and known failed entry conditions, and require missing RSI/volume ratio to be the remaining data blocker. Three-valued expression evaluation preserves AND/OR semantics with unknown observations; it selects research only and cannot authorize entries. Rank by number of blocked slots, then current five-minute USD volume, liquidity and discovery time. Keep the free primary feed first. Recheck the current pool, eligibility and observations after the request before storing evidence. The primary feed's cooldown and budget remain independent. The fallback, when enabled, admits only valid USD signals and tags provider/currency. Individual pools wait at least an hour between Codex attempts, limiting use to selective coverage rather than continuous full-universe polling.

Every network call reserves estimated compute units in an immediate SQLite transaction before the request: 11 for metadata (published base 10) and 5 for candles (base 4). Reservations survive failures/restarts; provider charges are not falsely reported as measured. Fixed query fields are deliberately small. Calendar-UTC limits are 200 reserved CU/day and 8,000/month, with no more than four request reservations in a rolling second. Account billing periods and any other clients may differ, so these are application budgets, not a billing guarantee. No plan upgrade, MPP payment, overage enrollment or subscription is made.

GraphQL errors invalidate partial data. Access/schema problems pause requests for an hour; 429/temporary failures pause a minute. Safe fixed error categories and aggregate sample/call/reserved-unit counts appear in authenticated Jarvis status and existing diagnostics. Requests are bounded to five seconds, 400KB JSON and no redirects. No arbitrary query text comes from generated strategies.

Impact counters begin with this update: successful fallback charts, charts completing at least one blocked rule, and charts matching at least one rule (30-day observation retention). These are chart counts, not entries. Actual open and closed paper entries carry a frozen codex_required flag only when the entry passed with Codex observations and failed without chart observations. This measures evidence dependency, not profitability or a causal comparison against another feed. Older entries are not retroactively attributed. Existing usage reservations are retained across the update.

Validation includes provider identity/freshness, quota persistence, partial-expression selection, capacity and known-filter exclusions, prioritized persistence, healthy-primary exclusion, and entry attribution. Live sample outcomes must be checked separately before fallback is enabled. No profit or trading-recovery promise follows from API authentication alone.

Sources verified November 25, 2026:
https://docs.codex.io/api-reference/queries/getbars
https://docs.codex.io/api-reference/queries/pairmetadata
https://docs.codex.io/api-reference/types/pair
https://docs.codex.io/concepts/authentication
https://docs.codex.io/concepts/compute-unit-costs
