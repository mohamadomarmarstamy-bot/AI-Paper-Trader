# Brave research on the paired laptop

Stop the worker with Ctrl+C before updating files or configuration; paper positions remain monitored on Railway. Use a Brave Search API plan whose monthly free credit and usage controls you have checked in its dashboard. Do not enable auto-reload or pay for added credits unless intended. Account activation may require a card. This integration uses the Search endpoint, not the separately priced Answers endpoint.

After obtaining the key, run local-worker/Configure-Brave.ps1 on the laptop. It prompts for a hidden key, preserves the paired worker token, and configures up to 30 sessions each UTC day with a 900-request monthly cap. Do not share config.json or the key. No Railway secret or server change is required for this switch. Restart the worker after changing its configuration.

Thirty is a ceiling, not a promise of 30 daily searches. Full or paused slots skip research. There remains one search per research attempt and a five-minute minimum between attempts. Failed requests consume a reserved monthly attempt. A 31-day month still stops at 900. The counter applies to this worker's persisted state only; it does not account for other API-key users or reinstallations. Keep provider-side spending controls enabled too.

Brave queries target Solana DEX research and transaction costs across websites, while the same local model analyzes returned snippets. No arbitrary pages are fetched and this is not a full-document research agent. Better retrieval does not establish better profitability.

Brave's standard published retention guidance prohibits keeping returned search data without additional rights. This worker uses Brave results temporarily in memory for local generation; it does not save or submit returned URLs, titles or snippets. The model is instructed not to quote reference content. Only generated rules, local usage counts and our own provider-level receipt are retained. The dashboard therefore identifies Brave as provider without claiming archived per-page evidence. Wikipedia behavior is unchanged. Hosted AI calls remain disabled.

Tests cover the 30-session daily limit, 900-request monthly cutoff and no retention/submission of Brave result content. Live Brave access and account-specific billing controls require verification on the user's laptop.
