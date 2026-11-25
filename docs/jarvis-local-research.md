# Local research release notes

The ASUS installation guide is [local-worker/README.md](../local-worker/README.md).

This change adds an outbound-only Ollama worker and ten fixed paper slots with interpreted strategy code, persistent source/version reports, automatic retirement/replacement, and a 100-distinct-coin evaluation floor. Existing paper quote execution and accounting are reused. Research entries yield when exit quotes are backed up. No live execution modules or wallet permissions are added.

Dedicated bearer endpoints are disabled until JARVIS_LOCAL_WORKER_TOKEN has at least 32 characters. They expose aggregate paper context and accept validated strategy expressions only. The browser dashboard remains under the existing session check. Submission is limited to ten active slots, deduplicated by parsed expressions, and request bodies are capped at 20 KB. Stored source links use HTTPS and text is rendered with textContent.

Local inference targets loopback only, unloads the model after each response, disables cloud model tags and makes no OpenAI calls. Free reference search is the default. The existing paid researcher now requires explicit JARVIS_RESEARCH_PROVIDER=openai and also waits for 100 coins before adopting a generated rule.

The restricted language has comparisons, short-circuit boolean operations and bounded numeric arithmetic. It prohibits calls, imports, attributes, subscripts, comprehensions, powers and future fields. This permits autonomous entry/exit code changes within the simulator, not arbitrary Python execution or new Python indicator modules.

Every position stores its own frozen rule version. Failed versions do not rewrite history or sell existing holdings immediately. They stop new entries, then release their slot when holdings close. Proven experiments continue; promotion only fills an empty/paused main strategy. Capital is virtual and funded once per slot.

Known limits: closed-only evaluation bias; observed prices and sampled stops; Wikipedia search is educational reference lookup rather than broad current-market research; local model output quality and memory use require the physical laptop benchmark; no Windows execution or deployed handshake has been observed yet. A browser launch in this workspace was blocked by OS socket restrictions, so browser layout automation could not run here; JavaScript syntax and server integration were checked separately.
