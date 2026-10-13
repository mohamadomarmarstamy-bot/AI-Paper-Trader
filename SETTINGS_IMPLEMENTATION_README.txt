AI PAPER TRADER — SETTINGS IMPLEMENTATION PACKAGE
=================================================

This package contains coordinated replacements for:
  backend/main.py
  backend/database.py
  backend/frontend/index.html
  backend/frontend/js/settings.js

Implemented:
- Atomic persistence for batches of application settings.
- All 11 numerical settings plus Pause New Entries in the authenticated settings API.
- Validated runtime settings with a short cache and existing defaults.
- Position sizing, per-order and per-position limits, open-position limit,
  stop-loss, take-profit, profit-lock, and trailing-stop values wired to settings.
- Daily profit/loss thresholds calculated from verified prior-session equity.
- Functional numeric controls in the Settings page.
- Existing session authentication and same-origin checks retained.
- Existing protective orders are not automatically canceled when a setting changes.

Before applying:
1. Make sure the project is on the expected branch and preserve all uncommitted work.
2. Create a fresh backup or commit/stash your current changes.
3. Extract this ZIP into the project root and allow the four files to be replaced.
4. Review `git diff` before committing. Do not blindly overwrite unrelated local edits.
5. Run:
     python -m py_compile .\backend\main.py .\backend\database.py
     node --check .\backend\frontend\js\settings.js
     git diff --check
6. Test the deployed PAPER environment after deployment. Do not switch to live trading.

Defaults:
position size 2%; max order 2.5%; max position 5%; max open positions 50;
stop loss 2%; take profit 4%; profit-lock trigger 0.75%; profit lock 0.25%;
trailing stop 1%; daily profit target 5%; daily loss limit 1%.

Note:
This package has passed syntax checks and focused database validation in isolation.
It has not been deployed or verified against your live Railway service or Alpaca PAPER
account. Complete review and deploy only after your normal safety checks.
