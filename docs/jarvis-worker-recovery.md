# Laptop worker recovery

Update only local-worker/worker.py. The server and existing config.json, worker-state.json and tokens do not need changing. Stop the local loop with Ctrl+C before replacing its file, then restart the normal command. Do not run two workers at once.

Generated proposals are now saved before submission. Network failures retain the proposal across restarts and retry after at least 60 seconds, without another search, model call or daily session charge. An accepted or duplicate result clears it. Full/paused responses wait five minutes. Validation failures (HTTP 422) discard invalid proposals; authentication failures retain them for a configuration repair. Provider idempotency is supplied by the server's existing rule fingerprint and duplicate handling.

Errors identify app context, app submission, Brave/Wikipedia search, or the local Ollama model and the HTTP status when available. Logs never include response bodies, credentials or full URLs. Brave result content remains transient; only the existing provider receipt is persisted, including for delivery retries.

No server deployment is needed for this laptop-only recovery change.
