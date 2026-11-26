# November 27 watchlist pilot

The owner handles the spare Android and an already authorized USB mirror. Use an already open watchlist. Confirm permission for the test and agree on a short observation window, the reference clock, and who will independently verify each update. Keep all unrelated phone content out of captures. Stop capture if the app blocks screenshots or if the watchlist cannot be isolated. Use manual observations with permission. Skip uncertain characters. No account setup or phone control is required by this tool.

On the cloud computer prepare a plain text allowlist of verified tradable stock symbols, one per line. For each distinct update assign a short event label, reuse it on repeat sightings, and record the earliest time a person actually saw it, including UTC offset. Use a new label for a new update on the same ticker. Avoid copying app text or images into the log. This pilot uses human confirmation. It has no OCR, feed connection, or order path.

Example using fabricated observations and a scratch directory containing the allowlist:

    python scripts/friday_watchlist_pilot.py --pilot-db watchlist-pilot.sqlite3 --allowlist symbols.txt --event fixture-01 --symbol AAPL --first-seen 2026-11-25T15:00:12Z --published 2026-11-25T15:00:00Z

Omit the publication time until an independent source confirms the exact timestamp. Repeating the command with that evidence attaches it to the original sighting. Report unknown delay as unknown. If a timestamp is rounded to the minute, leave the exact delay unknown. Record capture gaps and independently tally all reference events, including missed, misread, duplicate, and unverified rows. Compare verified event counts, correct symbols, misses and observed delay distribution before discussing future integration. The pilot database is disposable and has no app dependencies. End the mirror when the window closes.
