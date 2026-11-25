# PBH paper closeout

The owner requested a one-time closeout of all existing PBH paper holdings. On first focus-mode startup, fixed-preset holdings and holdings bound to failed/inactive local strategy versions receive an owner_closeout flag. Current active local research and Jarvis holdings are excluded. The action pauses preset entries and persists its completion marker; later restarts do not retarget holdings.

Owner closeouts request the full remaining quantity and close using the validated quote floor and existing simulated fees. They do not wait for the original strategy's profit/trim/stop condition. Missing quotes remain unresolved. The pre-existing owner-selected no-route paper write-off policy remains separate from quoted closes; UI and diagnostics report these separately. Real-copy execution is unaffected.

The scheduler reserves every third due exit attempt for active Jarvis/research when mixed with older work. Observed genuine exits keep the existing priority path. Owner closeouts do not independently block new research buys. Retries retain their cooldowns and invalid-parameter errors use a fifteen-minute backoff. Existing provider pacing is unchanged.

HTTP diagnostics recognize fixed categories from error, errorMessage, message, errorCode and code fields, without echoing their contents. Route absence, partial-amount routing and invalid parameters are distinct. Invalid local mint/amount inputs are rejected before any request. Classification references: https://developers.jup.ag/docs/guides/how-to-build-a-custom-swap-with-metis and https://github.com/jup-ag/docs/blob/main/docs/swap/order-and-execute.mdx .

Validation covers one-time PBH targeting, exclusion of active experiments, exact-size quoted closure and fees, no fabricated partial fills, reserved active capacity, urgent exit precedence, retry cooldowns and safe error categories.
