# Jarvis on the ASUS — setup and benchmark

This package is prepared for the ASUS i5-8250U / 8 GB RAM. It has NOT been benchmarked on that laptop. No factory reset is needed. Finish Windows updates and restart first.

## What runs where

- Laptop: Ollama with a small local model, reference search, and one strategy-generation job at a time.
- Railway: ten separate paper experiment slots, quotes, positions, persistent reports, and the main Jarvis paper portfolio.
- No live-money permissions, wallet keys, cloud AI calls, arbitrary generated Python execution, inbound ports, or automatic spending upgrades are included.

The model writes actual entry and exit expressions in a restricted Python-style language. The server validates and interprets them; it never executes model-written Python modules. Calls, imports, files, attributes, network access, future prices and modifications of the evaluator are unavailable. Arbitrary indicator/module generation remains a separate reviewed feature, not something this package claims to provide.

## 1. Install prerequisites

Download Python 3.12 or newer from https://www.python.org/downloads/windows/ and Ollama from https://ollama.com/download/windows. Open Ollama after installation. The worker requires Windows x64 and the Python launcher (`py`).

Extract the whole ZIP into a normal folder. Do not run files from inside the ZIP. Keep the `backend` and `local-worker` folders together.

In PowerShell, open the extracted `local-worker` directory and run:

```powershell
.\Setup.ps1
```

This creates a private Python environment, installs requests, and downloads `qwen2.5-coder:3b`. The published model download is about 1.9 GB; runtime memory is larger. Disk space for Python and the model is also required. If PowerShell blocks scripts, use these individual commands rather than changing machine-wide execution policy:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install "requests>=2.32,<3"
ollama pull qwen2.5-coder:3b
Copy-Item config.example.json config.json
```

## 2. Benchmark before connecting

```powershell
.\Benchmark.ps1
```

Or:

```powershell
.\.venv\Scripts\python.exe worker.py --benchmark
```

The benchmark performs one reference search and generates a validated strategy locally. It does not connect to Railway, start trades, or submit the strategy. Read `worker-state.json` for elapsed seconds, generation tokens/second, and the proposal. These measure speed and basic format validity, not trading skill. Send those benchmark numbers back before deciding whether to use this model continuously.

If 3B is too slow or runs out of memory, try the smaller 1.5B variant:

```powershell
ollama pull qwen2.5-coder:1.5b
```

Change `model` in config.json to `qwen2.5-coder:1.5b`, then rerun the benchmark. Smaller models may produce weaker strategies. Close unnecessary apps and keep the laptop on a hard, ventilated surface.

## 3. Deploy the matching server change and pair it

The backend changes in branch `feature/jarvis-autonomous-research` must be reviewed and deployed first. The ZIP alone cannot add server endpoints. No deployment or GitHub push is implied by this bundle.

Generate a dedicated random token on the laptop:

```powershell
py -3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put the same token into:

- Railway's `JARVIS_LOCAL_WORKER_TOKEN` variable on the AI-Paper-Trader service.
- `worker_token` in local-worker/config.json.

Keep that token private. Do not paste it into chat or commit config.json. It grants only the local-worker context and paper-strategy submission endpoints. Rotate it in both places to revoke an old installation. Keep Railway `JARVIS_RESEARCH_PROVIDER` unset for this local-only setup; setting it to `openai` explicitly enables the older billable cloud researcher.

Confirm config.json has the correct HTTPS app origin. The included production origin is https://ai-paper-trader-production-7465.up.railway.app. The worker never follows redirects with its token and never sends the token to search or Ollama.

## 4. Start

Enable the app's existing paper-run switch when ready, then:

```powershell
.\Start.ps1
```

Or:

```powershell
.\.venv\Scripts\python.exe worker.py
```

The terminal and Ollama must remain running. Leave the laptop awake, plugged in and connected; the display may turn off. Ctrl+C stops research. Railway keeps monitoring already open paper positions. No startup task or sleep-setting changes are made automatically.

The Jarvis page shows the worker's last contact, free experiment slots, hypotheses, sources, entry/exit expressions and progress. The Strategy Lab includes all ten research slots and their holdings/history.

## Research, costs and replacement

The default is up to ten sessions each UTC day, only when a slot needs a strategy, with at least five minutes between attempts. Requests that fail still count to prevent retry storms. The daily count and pending state persist through restarts. Change `sessions_per_day` from 1 to 100 in config.json if desired. Ten candidates can be filled on the first day if generation succeeds; invalid or duplicate proposals consume attempts and are retried in later sessions. Healthy/full slots do not trigger unnecessary searches.

Default search is Wikipedia reference search: free, key-free educational snippets, NOT Google, current coin news, or unrestricted web browsing. Availability and throttling depend on Wikipedia. Sources are research leads, not independently verified trading evidence. No arbitrary source pages are fetched by the worker. The AI only sees snippets, recent strategy versions and paper reports.

Optional broad web search: set `search_provider` to `brave` and put your Brave API key in `brave_api_key`. Check your own Brave plan and billing first. `brave_monthly_limit` is capped at 900 in this worker; it is a request quota, not a dollar guarantee or shared-account spending cap. It counts attempts before requests. No Brave key is needed for the default. There is no fallback to paid OpenAI calls.

## How paper experiments work

- Ten slots, each funded once with $2,000 virtual cash; $50 principal per trade plus existing modeled costs. No automatic funding replenishment.
- At most two open positions per research slot. Existing exit queues and provider pacing take priority; slots wait if exits are backed up.
- A rule only sees coins first observed after it was admitted. Entry and exit expressions freeze on each position. Editing a failed idea creates a new semantic version; renaming identical code cannot restart its history.
- A fixed sampled -10% safety stop and one-hour maximum holding time apply. Price gaps, missing routes and quote delays can produce larger losses; stops are not guaranteed fills. Existing paper no-route write-offs remain applicable.
- A profitability decision waits for 100 distinct completed coins. After that, the latest 100 closes form the evaluation window. Nonpositive net P/L retires the strategy. Positive net P/L with positive results in both chronological halves after removing each half's largest winner qualifies as proven. Positive but non-robust results keep testing.
- Zero closes and zero holdings after 48 hours marks a strategy inactive, not unprofitable. The worker researches replacements for empty slots. Sparse-entry strategies can take much longer than a day to reach 100 coins.
- Failed/inactive versions stop opening entries, while existing positions keep frozen exits. Slots cannot be reused until those positions close. Fixed slot cash carries over; failed experiments do not receive a fresh deposit.
- A proven version can populate an empty or paused main Jarvis strategy. Healthy active Jarvis strategies are retained. New policies affect future entries only. A local-derived main strategy pauses after 100 Jarvis closes with nonpositive net P/L and may be replaced by a different proven version.
- Open positions are excluded from profitability decisions, creating closed-trade selection bias. Inspect exposure and quote delays alongside scores. A passing paper experiment does not establish future or live profitability.

## Still to verify on your laptop

PowerShell execution, installed Python/Ollama versions, model availability, actual memory usage, generation speed, free-source reachability and a complete laptop-to-deployed-server handshake. Server/worker logic has automated fixture tests; no actual laptop run or live model output has been claimed.
