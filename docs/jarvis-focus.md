# Jarvis focus

The owner selected JARVIS_FOCUS_MODE=true for production. While enabled, only the hourly Jarvis research digest and Jarvis health / 30-minute inactivity emails may send. Stock reports, stock health/learning, original meme learning, copy inactivity, live exit notices, Jarvis code-proposal emails, and Jarvis backlog/timer emails remain paused at delivery, including already-queued messages. Research and trading records continue to be saved. Setting the flag to false restores their existing delivery controls; it does not automatically restart a trader.

On first startup with this flag, a transactional migration pauses new entries in the original meme paper bot, paper copier, and real copier. It preserves holdings, pending orders, cash, history, settings and exit monitoring. It runs before their workers start, records completion, and does not override a later explicit owner restart. It does not enable Jarvis or alter stock trading.

Jarvis inactivity measures successful buys or sells across the Jarvis Trader and local AI research portfolios. Quotes, scans and activity in the older comparison strategies do not reset it. Paper write-offs do not count as successful sells. One email is sent per 30-minute quiet episode while the paper engine is enabled. A separate stale-worker alert fires after 30 minutes without a completed engine cycle. Successful trading or a recovered heartbeat rearms the corresponding alert; pausing the engine cancels pending alerts and resuming starts a fresh grace period. Delivery shares the persistent, idempotent hourly outbox.

The four former strategy tabs now live as rows in Strategy Lab alongside the existing strategies and AI research slots. Old bookmarks open the matching Lab row. Jarvis Trader is the default page. Holdings and history are preserved.

Validation includes transactional pause preservation and one-time execution, transport suppression, independent idle and stale monitoring, pause/resume grace, the Jarvis regression suite, existing email tests and JavaScript syntax checks.
