# Jarvis investigations and strategy research

This release adds a server-side investigator using the existing private OPENAI_API_KEY and the Responses API with GPT-6.2 Sol. Enable JARVIS_BRAIN_ENABLED=true. It runs independently of the laptop, trading worker, and primary chart scheduler. The laptop's Ollama/Brave settings and 30-session allowance are unchanged. No new paid hosting service is provisioned.

## Operational incidents

Every 30 seconds, inspect paper-worker heartbeat, overdue executable exit checks, eligible chart coverage and persistent engine errors. Confirm queue/heartbeat/error symptoms for at least two minutes before opening an incident; require ten minutes for a zero-coverage gap affecting ten or more eligible candidates. A brief interruption creates no incident email. Intentional owner shutdown does not count as successful recovery.

Confirmed overdue monitoring automatically holds new Jarvis paper entries using a separate brain_pause. Sells, quote scheduling and route retries continue through the existing engine. The investigator cannot sell positions, write off a loss, change cash/accounting, alter risk limits, bypass provider cooldowns or turn live trading on. Temporary research cooldown extensions can only apply when current coverage reports primary rate limiting; existing longer cooldowns remain intact. Owner pause controls are preserved.

Investigation receives aggregate diagnostics, quote-failure categories, audited strategy outcomes, balanced retrospective winners/losers, bounded source excerpts and previous incident hypotheses. No environment values, credentials, raw provider bodies or wallet identities are supplied. Operational web research is restricted to official provider/runtime documentation domains. Model-proposed actions are rechecked against current conditions by the host. Each incident allows at most two cloud investigations, at least one hour apart. Cloud jobs use a persisted three-minute lease; model responses are limited to 4,500 output tokens, two search tool calls, 90 seconds and 300KB. Oversized prompts are rejected before reservations/network access.

Require at least three healthy observations over two minutes before marking resolved. Recovery verifies current monitoring; it does not prove a root cause or credit the model with a fix it did not apply. One opening and one resolution email per incident use a durable outbox, retries and stable Resend idempotency keys. A persistent incident gets one needs-attention update after an hour. These are Jarvis health notices, so existing quiet-hour rules for routine digests do not silence them.

The investigator is in-process: it cannot email or repair a completely unavailable hosting platform, or safely restart a hung process from inside that process. Those failures still require an external supervisor.

## Strategy loop

A cloud review is due once per 24 hours, subject to the same budget and incident priority. It can propose one distinct strategy, grounded in actual winners AND losers, data gaps and exit delays. Historical examples inform a hypothesis; they never count as forward validation. Up to three proposals can wait for a slot. Proposals older than 14 days expire.

The existing local strategy validator admits a proposal only into an available one of ten slots, with cash, semantic deduplication and safe expressions checked again in its own transaction. Forward evaluation starts at actual admission, not proposal time. Existing immutable safety stops, maximum hold, fees, write-offs, four-position cap, 100-coin checkpoint, loss retirement and robust promotion checks remain in force. Slot and laptop submissions may race; the existing unique slot constraint resolves admission safely. New proposals and promotions continue to appear in the existing hourly digest after actual admission.

## Code artifacts

For unfamiliar bugs, the model may propose one replacement function from a small source allowlist. The host constructs a diff against current source, records its SHA-256, and checks Python syntax without importing or running generated code. The proposal is stored with needs_functional_tests status. It is NOT deployed, marked fixed, or described as functionally tested. The system does not currently have an isolated functional-test/staging deployment runner. This is an explicit next gate before broader autonomous code repair.

## Budget and visibility

The NEW investigator shares a $25/month reservation budget across operational and strategy jobs, with a $2/day limit and a conservative $0.50 reservation per Responses request. Reserve in an immediate SQLite transaction before network access. Failed, interrupted and invalid responses keep their reservation. Limits are calendar UTC and survive restarts. At most 50 requests can be admitted per month; usage is not unlimited. Built-in OpenAI web searches belong to these investigator requests; they do not consume the laptop's Brave searches.

These are conservative application reservations, not measured billing or an account-wide provider spending guarantee. Prices/tool context can change. Existing stock-side AI clients, other API users, hosting, email and market-data charges are separate. Do not silently raise limits, enable a more expensive model or purchase a plan. If the investigator allowance is exhausted, automatic monitoring, recovery, incident email and local paper testing continue; cloud work waits for allowance.

Authenticated /jarvis-test-bot/brain provides incidents, citations, checks, proposals and reserved budget. The authenticated status endpoint uses no-store. The main Jarvis navigation links to this page. The existing runtime diagnostic logs only aggregate brain budget/configuration, call outcome and proposal counts; generated patches/narratives stay out of logs.

Disable JARVIS_BRAIN_ENABLED to stop new investigations; brain-only entry holds stop gating buys while all owner controls remain honored. Incident history and usage reservations remain durable. Enabling later resumes monitoring without resetting the budget.

## Validation

875 Solana tests passed, including persistence/budgets, transient confirmation, multiple healthy checks, owner pause preservation, idempotent incident mail retries, bounded cloud investigation, source attribution, rejection of unsupported actions/paths, non-execution of generated code, prospective strategy admission and owner-session protection. UI JavaScript syntax also checked. Live cloud access and provider billing availability must be verified separately after enabling.
