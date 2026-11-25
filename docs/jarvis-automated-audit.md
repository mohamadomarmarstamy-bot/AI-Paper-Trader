# Automated Jarvis audit — phase one

Prepared for review; deployment is intentionally deferred until the owner's next session. Related priority: #157.

The existing server diagnostic worker collects one observation per five-minute bucket (at its next scheduled check), keeps 30 days of snapshots, and recomputes a durable audit summary. No laptop changes, extra API requests or new AI/search calls are needed. Restarting within a sampled bucket cannot double-count observations.

Each strategy version has a separate report: lifetime closed-coin realized P/L, profit factor, P/L after removing its best coin, recorded no-route write-offs, measured overdue quote checks, and 24-hour missing-data/capacity observations. Owner-requested closeouts are separated from strategy-directed outcomes. Missing historical timing is unknown, never zero. Snapshot counts are repeated observations, not distinct opportunities or counts of missed trades. Correlation between late checks and losses does not establish causation.

The latest audit is available as `automated_audit` in the existing authenticated Jarvis status response. Scheduled hourly and overnight emails receive a compact audit line using the existing schedule and transport. This phase does not create a new email sender or change strategy admission/retirement/promotion.

Next phases remain tracked in #157: complete decision-time opportunity recording including skipped and later untradable coins; controlled comparisons of three distinct hypotheses; frozen forward tests; and uncertainty-aware evaluation. This first audit does not claim those phases are complete and does not infer hypothetical executable fills from old outcomes.
