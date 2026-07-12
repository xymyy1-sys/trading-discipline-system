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
- Time-stop base rules: sustained true-VWAP break, failed limit-up reseal, and 10:00 confirmation deadline.
- Cross-sector migration event: `SECTOR_MIGRATION_CONFIRMED`.
- Risk recovery event: `RISK_RECOVERY_CONFIRMED`.
- Minute-bar volume metrics: active buy/sell amount, attack efficiency, and volume acceleration.
- Frontend real-time risk event panel in the Today Decision workspace.

## Still Open

- Enable CI in GitHub after the PAT gets `workflow` scope. The workflow file exists locally at `.github/workflows/ci.yml` but cannot be pushed by the current token.
- Make minute-bar fetching production-hardened across trading days, non-trading days, ETFs, and temporary Eastmoney outages.
- Convert time-stop thresholds into configurable rules by script type and stage.
- Prefer structured stop levels from next-day plans, limit-up plans, and sell cards before text parsing.
- Add per-event confirmation windows by event type.
- Split minute-bar volume into attack segment and pullback segment for stricter pullback-volume evidence.
- Strengthen cross-sector migration scoring with original theme outflow, new theme inflow, stock weakening, and leader-switch evidence.
- Add a front-end SSE connection health indicator and recovery notification UX beyond the current event list.
- Add formal acceptance report exports for SSE demo, full single-stock intraday timeline, and T+1 validation.

## Latest Validation Commands

```bash
cd backend && PYTHONPATH=. .venv/bin/pytest -q
npm run lint
npm run test -- --run
npm run build
cd backend && PYTHONPATH=. .venv/bin/alembic -c alembic.ini current
```
