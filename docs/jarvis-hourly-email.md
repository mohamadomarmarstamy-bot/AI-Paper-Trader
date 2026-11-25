# Jarvis scheduled research email

Routine paper briefs send on the clock hour, Eastern time (America/New_York), checked every 20 seconds. The midnight brief covers the preceding period. No routine digests or retries send from 1 AM through 8:59 AM. At 9 AM, send one compact overnight summary with an hourly table of closes, separate research and Jarvis realized P/L, and strategy-event counts. Normal hourly briefs resume at 10 AM. Clock-hour scheduling prevents minute drift across restarts; timezone conversion handles daylight saving transitions.

Positive realized values are green, negative red, and zero gray, including period and lifetime cards and strategy scorecards. Plain-text fallback retains signed amounts.

On migration, a minute-offset legacy schedule advances to the next permitted clock hour. The 9 AM report includes midnight onward even if old schedules had already sent part of that period. Unsent, unattempted older messages are superseded by the catch-up summary; attempted but uncertain older deliveries are retained for attention rather than replayed alongside the overnight report. Delivery success still requires a provider acceptance ID.

Urgent Jarvis health/inactivity and exit backlog alerts remain independent of routine digest quiet hours. No laptop worker changes are required.
