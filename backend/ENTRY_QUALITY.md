# Entry quality policy v2

This change fixes selection checks in the existing paper trader. It does not
train a predictive model or establish an improvement in trading returns.

## Changed behavior

- Momentum candidates must pass the same BUY, score, confidence, rank and ATR
  requirements as other candidates. Previously their momentum flag alone could
  bypass the adjusted score, confidence and rank requirements.
- Both entry checks use `evaluate_entry_quality`: before requesting news and
  after applying the negative-news adjustment. Learning and bearish-regime
  adjustments therefore also apply to momentum entries.
- Missing, nonfinite, out-of-range or malformed selection inputs reject entry.
  Rank must be a positive integer; ATR must be positive and within the existing
  configured limit. A missing freshness flag also rejects entry.
- Expired scanner cache and failed-refresh fallback results are explicitly
  marked stale. They remain available to the dashboard and existing exit logic,
  but cannot qualify for new entries.
- Score tightening can no longer reduce an existing minimum above 90.
- Rejections include the failed checks. Successful entry context includes
  `entry_quality.policy_version = entry_quality_v2`, the thresholds used, and
  an explicit label that confidence is heuristic, not a win probability.

No broker settings, position sizes, exit rules, database schema, or scanner
universe were changed. The existing strategy tags are retained; use the entry
policy version in event details to distinguish entries after this change.

## Validation

From the repository root:

```powershell
python -m unittest discover -s backend -p test_entry_quality.py -v
python -m py_compile backend/main.py backend/scanner.py backend/entry_quality.py
git diff --check
```

Tests exercise the pure policy plus the actual entry-decision section and
scanner cache functions extracted from source, avoiding application startup,
broker access and database writes. These are targeted regressions, not a full
application or Railway deployment test.

## Measuring the effect

Expect fewer momentum entries, especially movers with HOLD/SELL signals,
low scores, or ranks outside the configured range. Review skipped candidates
and record outcomes for rejected as well as accepted setups. Compare forward
paper-trading results using policy-version-tagged entry events, including net
returns, drawdown, number of trades, and regime/time-of-day differences. Do not
interpret a change in win rate from a small sample as proof of improvement.

Scanner freshness here means cache/refresh status only. It does not establish
that provider bars or broker quotes are current. The scanner still uses daily
bars, and its confidence score remains an uncalibrated heuristic. Historical
out-of-sample evaluation and provider timestamp validation remain future work.

These local changes require a restart or deployment to affect a running app.
