# Jarvis hourly research email

The server sends an HTML email and plain-text alternative to the existing owner alert address. The first report covers the preceding hour; subsequent reports run every hour. Times are shown in America/New_York. No laptop update is required.

Each report includes accepted research proposals and their hypotheses and rules, strategies added or retired, duplicate submissions, promotions, progress toward 100 distinct closed coins, open holding counts, and realized paper P/L by strategy version. Main Jarvis results are separate from experiment totals. Open-position mark changes are excluded. Retired strategies retain their history. Research attempts that never submit to the server are not fully visible. Brave result text is not retained or reproduced; its provider receipt appears instead.

Delivery uses existing RESEND_API_KEY, HEALTH_ALERT_EMAIL_FROM and HEALTH_ALERT_EMAIL_TO. JARVIS_HOURLY_EMAIL_ENABLED defaults to true; false disables this feature independently of other alerts. A provider acceptance is not confirmation of inbox delivery.

Schedules and frozen email payloads live in the persistent Jarvis SQLite database. A restart preserves the next deadline. Downtime produces one catch-up report covering the gap. Failed deliveries retry with the same payload and provider idempotency key; after 23 hours of uncertain delivery the message is marked for attention rather than risking a resend after the provider deduplication window. Status exposes hourly_email with next_at, last_accepted, pending and attention counts, without credentials or message contents.

Validation: tests cover cadence, restart persistence, retries and immutable payloads, provider acceptance, version-specific accounting, lifecycle events, HTML escaping, configuration switches, downtime, expired delivery attempts and standalone laptop validator compatibility.
