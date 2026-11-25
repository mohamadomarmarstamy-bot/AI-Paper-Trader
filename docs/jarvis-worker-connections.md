# Laptop app connection recovery

App requests ask to close their connection after each response instead of reusing a socket across minute-long polling gaps. A context GET retries a connection error or timeout once after closing the connection pool. HTTP errors are not immediately retried. Strategy POST delivery continues through the existing saved-proposal retry mechanism, avoiding an immediate ambiguous replay or another paid search.

Full research slots now report `slots_full`; a disabled engine reports `paper_engine_paused`. Both skip research and model generation. Existing Brave content retention, quotas, proposal persistence and local-only model calls are preserved.

Install only local-worker/worker.py on the laptop, stop its existing loop first, and restart it. No server deployment or config replacement is required.
