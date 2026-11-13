# Wallet copyability evidence

Open **Real Copy Trade → Trade performance → Wallet copyability · evidence**. The table compares actual net SOL, actual completed positions/wins, entry premiums, realistic simulated SOL and original paper USD. Select a wallet for its realized drawdown, completed-position loss streak, profit factor, date coverage, open positions and execution failures. Refresh/pagination preserves the selection when the wallet remains in the report.

This is a read-only view of the same database snapshot used by the performance report. It adds no provider requests, worker, source enrollment or trading/settings mutation. Current rosters and sources retained only in history are included. Actual data is scoped to the execution wallet; original paper keeps its own full history, and realistic simulation uses its forward history. Amounts and currencies remain separate.

## Evidence labels

- **Accounting incomplete:** unknown sale allocation or failed-transaction fees prevent a reliable net result.
- **Reconciliation pending:** actual wallet results do not reconcile with the current ledger.
- **Paper only:** no confirmed actual buys for a source currently restricted to paper.
- **Insufficient data:** fewer than 20 completed actual positions or fewer than seven distinct Eastern close dates.
- **Manual exits included:** the history gate is met but manual external sales or archived manual adjustments affect results; this does not establish unattended copy performance.
- **Observed positive/negative/break-even net:** the above history gate is met, using actual allocated realized net after failed fees. This display rule is conservative bookkeeping, not statistical proof or a future-return prediction.

Rows with an observed-net label precede smaller samples. Within evidence groups, actual net determines the display order. Simulated profit never promotes a wallet, and no numeric 0–100 score or automatic eligibility change is produced.

## Accounting and risk

Actual net includes finalized sale allocations, paid failed-transaction fees once and audit-validated archived manual adjustments. Partial exits contribute to realized P/L but are grouped into their original position for completed wins and loss streaks. Failed buy fees affect wallet net and realized drawdown; they are not fictitious completed token positions. Completed-position profit factor excludes those unassociated failed buy fees, which remain visible separately. No completed losses yields an explicit label rather than infinity.

Realized drawdown starts from zero cumulative realized P/L and excludes open holdings, deposits, withdrawals and unrealized marks. Equal settlement timestamps are grouped; this is not tick-level account-equity drawdown. Missing settlement dates or amounts leave drawdown unavailable. Loss streaks order fully closed positions by their retained settlement times; missing times and mixed-sign ties leave them unavailable. Historical timestamps use receipt block time, with recorded observed-finalization timing as a fallback. Eastern date coverage counts completed positions, not elapsed idle days.

Archived manual adjustments stay in net P/L. The audit does not expose their exact settlement time, so review dates are never substituted for trade dates; affected risk histories remain incomplete. Original paper excludes voided closes, groups staged exits and supports legacy full closes without lot IDs. Realistic exit sums must reconcile with their saved realized result. Missing data stays unavailable rather than becoming zero risk.

Validation covers the two-part history gate, single large winners, fees overwhelming winning positions, partial-exit grouping, unknown allocations/fees/times, archived adjustments, currency/owner separation, voided paper records, simulation consistency, ambiguous timestamps and read-only empty reports. Browser coverage includes selection persistence, incomplete/empty states, safe wallet-name rendering and desktop/mobile overflow.
