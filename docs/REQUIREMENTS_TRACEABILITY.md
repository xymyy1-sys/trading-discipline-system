# V2.0 / V2.1 / V2.2 Requirements Traceability

This matrix is derived from all three supplied requirement documents. A feature is marked complete only when code and automated evidence exist; deployment-only checks remain separate from product implementation.

## Complete in code

| Requirement area | Evidence |
| --- | --- |
| Expectation stages and editable thresholds | `ExpectationSnapshot`, versioned `ExpectationRule`, stage refresh APIs and editor |
| Real minute evidence and VWAP guard | Eastmoney minute bars, Sina fallback, explicit reliable/estimated flags, persisted capture quality |
| Intraday evidence trajectory | background collector, evidence events, state history, SSE stream and recovery UI |
| High-open failed-breakout model | yellow/orange/red evidence states and 600584 replay checkpoints |
| Position execution state machine | persisted state transitions, profit protection, structure/hard/time stops, executable position ratios |
| T trading and T+1 | positive/reverse/no-T eligibility, yesterday/today quantities, sell-buyback lifecycle, permanent reduction |
| Recommendation feedback | active alerts, acknowledgement, executed/partial/deferred/ignored feedback and review statistics |
| Capital migration | multi-evidence weighted confirmation and `/api/market/capital-rotation` |
| Candidate pools | evidence-based A/B/C/D classification |
| Trading scripts | 12 seeded editable and versioned templates |
| Replay and calibration | persisted replay, effectiveness APIs, sample gates and 600584 acceptance checkpoints |
| Risk position sizing | structure-stop risk budget and script/market/stock/sector/liquidity caps |
| Consensus/profit pressure | recent-return, opening-expectation, VWAP and turnover-based model in decision card |
| Daily volume-price breadth | persisted MA5/10/20, 5/10-day returns, 20-day-high distance, historical volume ratio and transparent 30-day volume-weighted chip estimates |
| Account-level risk | daily baseline, loss thresholds, synchronized holding degradation and stop-loss count |
| Data provenance | source, latency, stale/degraded/estimated/complete flags, payload hash and provider health |
| Security | signed HttpOnly login, origin checks, rate limits, same-origin API, private backend port, security headers |
| Operation audit | hash-chained write audit log and chain verifier |
| Acceptance export | protected downloadable JSON report, migration/T+1/SSE/replay/audit evidence |

## Partially complete and still being hardened

| Requirement area | Current boundary / remaining work |
| --- | --- |
| Exchange microstructure fields | free-float turnover, true tick-direction active flow and large-order net values require a provider that exposes those raw fields; inferred values are labeled estimated and never presented as official |
| Sector evidence chart | sector flow curves, peaks and pullbacks exist; event overlay and sector VWAP completeness still require final UI acceptance |
| Parameter auto-calibration | statistics and sample gates exist; automatic parameter writes remain intentionally gated until at least 20 valid samples |
| Production availability | code is deployable; HTTPS certificate, firewall verification, database backup and real-server smoke tests must run on the target server |

## Deliberate non-goals

- No broker API and no automatic order placement.
- No deterministic recommendation when required real data is missing.
- No synthetic market curve presented as real data.
- Single-user authentication by default; multi-user roles are optional only if the system is later shared.
