# V2 P0 Hardening Status

Status: V2 first-phase foundation completed, P0 hardening in progress.

Deferred by request:
- Historical market replay.
- Changdian Technology 600584 dedicated replay report.

## Completed

- Background intraday collector with status and manual run APIs.
- Intraday collection run records.
- VWAP reliability fields: source, minute bar count, reliable flag.
- Data downgrade guard: no deterministic reduce, exit, or T-trade signal when true minute data is missing.
- Profit protection tracking: maximum floating profit, maximum time, day maximum profit, day maximum time.
- T+1 sellable quantity based on trade logs.
- Event grouping, priority, cooldown, occurrence count, and confirmation flag.
- SSE event stream at `/api/intraday-events/stream`; default mode streams only new events after connection.
- Profit protection and stop-level APIs.
- Intraday review API at `/api/stocks/{code}/intraday-review`.
- Script-aware stop parsing from holding discipline text.
- Structured stop levels from next-day plans and sell cards, before text parsing fallback.
- Time-stop base rules: sustained true-VWAP break, failed limit-up reseal, and script-aware confirmation deadline.
- Per-event confirmation policy for immediate, repeated, and cooldown-based risk events.
- Cross-sector migration event: `SECTOR_MIGRATION_CONFIRMED`.
- Risk recovery event: `RISK_RECOVERY_CONFIRMED`.
- Minute-bar volume metrics: active buy/sell amount, attack efficiency, volume acceleration, attack amount, pullback amount, and pullback sell ratio evidence.
- Frontend real-time risk event panel in the Today Decision workspace.
- V2.2 first batch:
  - Added `position_state_history` with old/new state, reason, evidence, and timestamp.
  - Execution state refresh now records state transitions instead of only overwriting the latest state.
  - Added `IntradayEvidenceEngine` service to centralize quote collection, volume-price snapshot, execution state refresh, and one intraday evidence snapshot per collection.
  - Intraday sample events use `INTRADAY_EVIDENCE_SNAPSHOT` with price, volume, VWAP, expectation state, volume-price state, sector state, and action.
  - Added `HIGH_OPEN_FAILED_BREAKOUT` real-time pattern detection with yellow/orange/red risk evidence for high-open failed breakout scenarios.
  - Added `/api/holdings/{holding_id}/state-history` for acceptance checks.
- V2.2 second batch:
  - Standardized reverse T output to `REVERSE_T` while accepting old `INVERSE_T` inputs and stored values.
  - Added dedicated `TTradingEngine` service for T eligibility, plan creation, plan updates, and T-type normalization.
  - Expanded expectation vocabulary with `EXTREME_STRONG` and `EBB`; actual result now uses V2.2 core labels `STRONGER`, `MATCHED`, `WEAKER`, and `INVALID` while old labels remain readable.
  - Execution and review calibration treat `INVALID` as expectation failure/risk evidence.
  - Cross-sector migration now requires at least three evidence criteria and outputs a confidence percentage before emitting `SECTOR_MIGRATION_CONFIRMED`.

## Still Open

- Enable CI in GitHub after the PAT gets `workflow` scope. The workflow file exists locally at `.github/workflows/ci.yml` but cannot be pushed by the current token.
- Make minute-bar fetching production-hardened across trading days, non-trading days, ETFs, and temporary Eastmoney outages.
- Expand time-stop thresholds into editable user-facing rule templates by script type and stage.
- Add explicit UI display of which stop source was used: next-day plan, sell card, text script, or fallback candidate.
- Persist attack/pullback segment metrics into dedicated columns if later analytics need filtering and aggregation.
- Strengthen cross-sector migration scoring with original theme outflow, new theme inflow, stock weakening, and leader-switch evidence.
- Add a front-end SSE connection health indicator and recovery notification UX beyond the current event list.
- Add frontend state-history timeline and intraday evidence trajectory display.
- Continue deeper TTradingEngine work: positive/reverse T execution feedback and UI guardrails.
- Continue expectation transition UI polish and editable thresholds by script type.
- Add formal acceptance report exports for SSE demo, full single-stock intraday timeline, and T+1 validation.

## Latest Validation Commands

```bash
cd backend && PYTHONPATH=. .venv/bin/pytest -q
npm run lint
npm run test -- --run
npm run build
cd backend && PYTHONPATH=. .venv/bin/alembic -c alembic.ini current
```
