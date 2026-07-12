# V2 P0 Hardening Status

Status: V2 first-phase foundation completed, P0 hardening in progress.

Historical replay is implemented against persisted evidence. A complete 600584 acceptance pass still requires the target trading-day evidence dataset to be present.

## Completed

- Added repository CI for frontend lint/test/build, backend pytest, and empty-database Alembic migration validation.

- Security hardening first batch:
  - Added single-user login with server-signed, HttpOnly, SameSite session cookies.
  - Protected all holdings, trades, plans, market, checks, and stock APIs; only health and authentication remain public.
  - Added login rate limiting and unsafe-request origin validation.
  - Production frontend now uses same-origin `/api` instead of bypassing Nginx through public port 8000.
  - Docker no longer publishes backend port 8000; Nginx adds baseline browser security headers.
  - Deployment fails closed when `AUTH_PASSWORD` or `AUTH_SECRET` is missing or weak.
- SSE recovery UX now records interruptions, automatic recovery time, and recovery count in the Today Decision workspace.
- T execution feedback now enforces guarded quantities and a one-way lifecycle: planned, sold waiting buyback, partial buyback, completed, or permanent reduction.
- Positions UI now loads active T plans and records guarded sell, cumulative buyback, and permanent-reduction feedback.
- Added editable expectation threshold rules scoped by script type, stage, and base expectation, with an editor in the stock decision workspace.
- Added active-alert and acknowledge APIs with latest-per-holding deduplication, expiry filtering, and execution-feedback status.
- Today Decision now shows unacknowledged operation recommendations and records explicit user acknowledgement.
- Added `/api/candidates` with evidence-based A/B/C/D scoring from expectation, reliable minute VWAP, execution state, and data quality.
- Stock Selection workspace now includes a candidate-pool panel with positive evidence and explicit exclusion reasons.
- Strengthened capital-migration confirmation with weighted source outflow/pullback, target inflow, stock weakness, sector ebb, ranking, risk, and leader-switch evidence.
- Added `/api/market/capital-rotation` with per-holding confidence and confirmation details.
- Added 12 seeded, editable, versioned trading strategy templates with complete environment, expectation, auction, volume-price, position, stop, invalidation, holding, and forbidden-action fields.
- Stock Selection workspace includes a strategy-template editor and versioned save workflow.
- Added `ReplayEngine` and `/api/replay/{code}` to merge historical expectation, volume-price, event, state-transition, and recommendation evidence into a time-ordered replay.
- Added 600584 acceptance checkpoints and a visual replay workspace under Review Calibration.
- Added expectation, volume-price, and execution effectiveness APIs with explicit sample counts and a 20-sample auto-calibration gate.

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
- V2.2 frontend traceability batch:
  - Today Decision workspace now loads `/api/stocks/{code}/intraday-review` for current holdings and displays a compact intraday evidence trajectory.
  - SSE connection status now shows stream-ready/new-event timestamps and refreshes the matching stock review after new risk events.
  - Holding execution cards already display state transitions, intraday events, and T-trade quantity basis.
- V2.2 stop-source traceability batch:
  - Added `stop_source` and `stop_source_detail` to execution states and stop-level APIs.
  - Execution state now marks whether stops came from next-day plan, sell card, text script, or fallback candidates.
  - Positions and stock decision card UIs display the stop source and detail instead of relying only on evidence text.
- V2.2 minute-bar reliability batch:
  - Eastmoney 1-minute bars now filter by latest trading day instead of calendar today, so weekends/non-trading days can still use the latest valid intraday bars.
  - Quote metadata now records `minute_bar_status`, `minute_bar_trade_date`, and fetch errors, making missing minute data an explicit downgrade reason.
  - Shanghai/Shenzhen ETF secid mapping is covered by tests.
- V2.2 editable time-stop rules batch:
  - Added `time_stop_rules` with default, breakout, and trend templates.
  - Time-stop execution now reads editable confirmation deadline, sustained VWAP-break minutes, confirming bar count, observation window, and failed reseal threshold.
  - Added `/api/time-stop-rules` list/update APIs and a Positions workspace rule editor.
- V2.2 structured minute-segment metrics batch:
  - Persisted attack amount, pullback amount, pullback/attack ratio, and pullback sell ratio on volume-price snapshots.
  - Stock decision cards now display attack/pullback segment metrics alongside VWAP and drawdown.
  - Segment metrics remain derived only from real minute bars or explicit quote fields; missing minute data still downgrades deterministic signals.

## Still Open

- Put the public deployment behind HTTPS, set `AUTH_COOKIE_SECURE=true`, close firewall port 8000, and rotate any previously exposed credentials.
- Add optional multi-user roles and an immutable audit log if the system will be shared with other operators.

- Continue minute-bar production monitoring: retries/backoff metrics, provider health history, and alternate provider support beyond Eastmoney.
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
