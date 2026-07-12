from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.helpers.execution import build_position_execution_state
from app.api.helpers.holdings_calc import _find_holding_by_code
from app.api.helpers.decision import (
    _json_list,
    build_expectation_snapshot,
    create_expectation_snapshot,
    current_expectation_stage,
    decision_card,
    quote_for_code,
    update_expectation_snapshot,
    ensure_expectation_rules,
)
from app.api.helpers.volume_price import build_volume_price_snapshot
from app.core.database import get_db
from app.models.trading import ExpectationRule, ExpectationSnapshot, Holding, IntradayEvidenceEvent, NextDayPlan, PositionExecutionState, VolumePriceSnapshot
from app.schemas.trading import (
    ExpectationSnapshotIn,
    ExpectationSnapshotOut,
    ExpectationSnapshotUpdate,
    ExpectationRuleIn,
    ExpectationRuleOut,
    IntradayEvidenceEventOut,
    IntradayReviewOut,
    StockDecisionCardOut,
    VolumePriceSnapshotOut,
    CandidateOut,
    WatchlistRecommendationOut,
    ReplayReportOut,
)

router = APIRouter()


@router.get("/watchlist-recommendations", response_model=list[WatchlistRecommendationOut])
def watchlist_recommendations(db: Session = Depends(get_db)) -> list[WatchlistRecommendationOut]:
    from app.services.market_data import MarketDataProvider

    provider = MarketDataProvider()
    executor = ThreadPoolExecutor(max_workers=2)
    theme_future = executor.submit(provider.theme_radar)
    ladder_future = executor.submit(provider.limit_up_ladder)
    done, pending = wait((theme_future, ladder_future), timeout=22)
    try:
        radar = theme_future.result() if theme_future in done else None
    except Exception:
        radar = None
    try:
        ladder = ladder_future.result() if ladder_future in done else None
    except Exception:
        ladder = None
    for future in pending:
        future.cancel()
    executor.shutdown(wait=False, cancel_futures=True)

    holding_codes = {row.code for row in db.query(Holding.code).all()}
    rows: dict[str, dict] = {}
    theme_source = radar.source if radar else "题材数据不可用"
    for theme in (radar.themes[:20] if radar else []):
        for stock in theme.core_stocks:
            if not stock.code:
                continue
            row = rows.setdefault(stock.code, {
                "code": stock.code, "name": stock.name, "score": 0, "theme": theme.name,
                "role": stock.role, "limit_level": 0, "limit_quality": "未进入涨停梯队",
                "fund_signal": "", "reasons": [], "risks": [], "sources": set(),
            })
            theme_points = min(45, round(theme.score * 0.45))
            rank_points = max(0, 12 - theme.rank)
            role_points = 12 if any(word in stock.role for word in ("龙头", "核心", "前排")) else 5
            row["score"] += theme_points + rank_points + role_points
            row["reasons"].append(f"{theme.name}题材排名第{theme.rank}，强度{theme.score}分")
            row["reasons"].append(f"题材角色：{stock.role or '核心股'}")
            if theme.net_inflow > 0:
                row["score"] += 8
                row["fund_signal"] = f"题材净流入{theme.net_inflow:.2f}亿"
                row["reasons"].append(row["fund_signal"])
            else:
                row["risks"].append("题材资金尚未形成净流入确认")
            row["sources"].add(theme_source)

    ladder_source = ladder.source if ladder else "涨停数据不可用"
    for group in (ladder.groups if ladder else []):
        for stock in group.stocks:
            if not stock.code:
                continue
            row = rows.setdefault(stock.code, {
                "code": stock.code, "name": stock.name, "score": 0,
                "theme": (stock.concepts[0] if stock.concepts else stock.industry), "role": "涨停前排",
                "limit_level": 0, "limit_quality": "", "fund_signal": "",
                "reasons": [], "risks": [], "sources": set(),
            })
            row["limit_level"] = max(row["limit_level"], stock.consecutive_limit_days, group.level)
            quality_score = min(20, row["limit_level"] * 6)
            if stock.break_count == 0:
                quality_score += 10
                row["limit_quality"] = "封板稳定、未炸板"
            else:
                quality_score -= min(18, stock.break_count * 6)
                row["limit_quality"] = f"炸板{stock.break_count}次"
                row["risks"].append(row["limit_quality"])
            if 3 <= stock.turnover <= 22:
                quality_score += 6
            elif stock.turnover > 30:
                quality_score -= 8
                row["risks"].append(f"换手率{stock.turnover:.1f}%偏高")
            row["score"] += quality_score
            row["reasons"].append(f"{group.label}，{row['limit_quality']}")
            row["sources"].add(ladder_source)

    outputs: list[WatchlistRecommendationOut] = []
    for row in rows.values():
        if "ST" in row["name"].upper():
            row["score"] -= 50
            row["risks"].append("风险警示股票不纳入自动观察池")
        if row["code"] in holding_codes:
            row["score"] -= 15
            row["risks"].append("当前已持仓，应转入持仓执行而非新增观察")
        score = max(0, min(100, int(row["score"])))
        tier = "重点观察" if score >= 70 and not any("风险警示" in risk for risk in row["risks"]) else "普通观察" if score >= 50 else "暂不纳入"
        outputs.append(WatchlistRecommendationOut(
            code=row["code"], name=row["name"], score=score, tier=tier,
            theme=row["theme"], role=row["role"], limit_level=row["limit_level"],
            limit_quality=row["limit_quality"], fund_signal=row["fund_signal"],
            reasons=list(dict.fromkeys(row["reasons"])), risks=list(dict.fromkeys(row["risks"])),
            source=" + ".join(sorted(row["sources"])),
            updated_at=max([value for value in (radar.updated_at if radar else None, ladder.updated_at if ladder else None) if value is not None], default=None),
        ))
    return sorted(outputs, key=lambda item: (-item.score, item.code))[:30]


@router.get("/replay/{code}", response_model=ReplayReportOut)
def replay_stock(code: str, trade_date: str, db: Session = Depends(get_db)) -> ReplayReportOut:
    from app.services.replay_engine import ReplayEngine
    return ReplayEngine(db).replay(code, trade_date)


@router.get("/candidates", response_model=list[CandidateOut])
def list_candidates(db: Session = Depends(get_db)) -> list[CandidateOut]:
    targets: dict[str, str] = {}
    for row in db.query(NextDayPlan).order_by(NextDayPlan.updated_at.desc()).limit(300).all():
        targets.setdefault(row.code, row.name)
    for row in db.query(Holding).all():
        targets.setdefault(row.code, row.name)

    outputs: list[CandidateOut] = []
    for code, name in targets.items():
        expectation = db.query(ExpectationSnapshot).filter(ExpectationSnapshot.code == code).order_by(ExpectationSnapshot.created_at.desc()).first()
        volume = db.query(VolumePriceSnapshot).filter(VolumePriceSnapshot.code == code).order_by(VolumePriceSnapshot.captured_at.desc()).first()
        execution = db.query(PositionExecutionState).filter(PositionExecutionState.code == code).order_by(PositionExecutionState.updated_at.desc()).first()
        score = 50
        reasons: list[str] = []
        exclusions: list[str] = []
        expectation_result = expectation.expectation_result if expectation else "UNKNOWN"
        if expectation_result in {"STRONGER", "MATCHED"}:
            score += 20
            reasons.append(f"预期状态：{_candidate_state_label(expectation_result)}")
        elif expectation_result in {"WEAKER", "INVALID"}:
            score -= 30
            exclusions.append(f"预期状态：{_candidate_state_label(expectation_result)}")
        else:
            score -= 10
            exclusions.append("缺少预期证据")

        volume_state = volume.pattern if volume else "UNKNOWN"
        data_quality = volume.data_quality if volume else "missing"
        if volume and volume.vwap_reliable:
            score += 15
            reasons.append("真实分钟均价线可靠")
        else:
            score -= 15
            exclusions.append("真实分钟均价线不可用")
        if execution:
            if execution.state in {"EXIT_REQUIRED", "REDUCE_REQUIRED", "EXPECTATION_INVALIDATED", "STOP_LOSS_WARNING"}:
                score -= 35
                exclusions.append(f"执行状态：{_candidate_state_label(execution.state)}")
            elif execution.state in {"NORMAL_HOLD", "PROFIT_EXPANSION"}:
                score += 10
                reasons.append(f"执行状态：{_candidate_state_label(execution.state)}")
        score = max(0, min(100, score))
        pool = "A" if score >= 75 and not exclusions else "B" if score >= 55 else "C" if score >= 35 else "D"
        outputs.append(CandidateOut(
            code=code,
            name=name,
            pool=pool,
            score=score,
            expectation_result=expectation_result,
            volume_price_state=volume_state,
            execution_state=execution.state if execution else "",
            data_quality=data_quality,
            reasons=reasons,
            exclusions=exclusions,
            updated_at=max([value for value in (expectation.created_at if expectation else None, volume.captured_at if volume else None, execution.updated_at if execution else None) if value is not None], default=None),
        ))
    return sorted(outputs, key=lambda item: (-item.score, item.code))


def _candidate_state_label(value: str) -> str:
    return {
        "STRONGER": "强于预期", "MATCHED": "符合预期", "WEAKER": "弱于预期",
        "SLIGHTLY_WEAKER": "略弱于预期", "INVALID": "预期证伪", "UNKNOWN": "未知",
        "EXIT_REQUIRED": "必须退出", "REDUCE_REQUIRED": "必须减仓",
        "EXPECTATION_INVALIDATED": "预期失效", "STOP_LOSS_WARNING": "止损警告",
        "NORMAL_HOLD": "正常持有", "PROFIT_EXPANSION": "利润扩张",
        "DEGRADED_DATA_OBSERVATION": "数据降级观察",
    }.get(value, value or "未知")


@router.get("/expectation-rules", response_model=list[ExpectationRuleOut])
def get_expectation_rules(db: Session = Depends(get_db)) -> list[ExpectationRule]:
    return ensure_expectation_rules(db)


@router.post("/expectation-rules", response_model=ExpectationRuleOut)
def upsert_expectation_rule(payload: ExpectationRuleIn, db: Session = Depends(get_db)) -> ExpectationRule:
    if not (payload.severe_underperform_threshold <= payload.underperform_threshold < payload.expected_open_low <= payload.expected_open_high < payload.outperform_threshold):
        raise HTTPException(status_code=422, detail="expectation thresholds must be strictly ordered")
    row = db.query(ExpectationRule).filter(
        ExpectationRule.script_type == payload.script_type,
        ExpectationRule.stage == payload.stage,
        ExpectationRule.base_expectation == payload.base_expectation,
    ).first()
    if row is None:
        row = ExpectationRule(**payload.model_dump())
        db.add(row)
    else:
        for key, value in payload.model_dump().items():
            setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


@router.get("/stocks/{code}/decision-card", response_model=StockDecisionCardOut)
def get_stock_decision_card(code: str, db: Session = Depends(get_db)) -> StockDecisionCardOut:
    return decision_card(db, code)


@router.get("/stocks/{code}/expectation", response_model=ExpectationSnapshotOut)
def get_stock_expectation(code: str, db: Session = Depends(get_db)) -> ExpectationSnapshotOut:
    return build_expectation_snapshot(db, code, stage=current_expectation_stage())


@router.get("/stocks/{code}/volume-price", response_model=VolumePriceSnapshotOut)
def get_stock_volume_price(code: str, db: Session = Depends(get_db)) -> VolumePriceSnapshotOut:
    quote = quote_for_code(code)
    name = str(quote.get("name") or code)
    return build_volume_price_snapshot(db, code, name=name, stage=current_expectation_stage(), quote=quote)


@router.post("/expectations", response_model=ExpectationSnapshotOut)
def post_expectation_snapshot(payload: ExpectationSnapshotIn, db: Session = Depends(get_db)) -> ExpectationSnapshotOut:
    return create_expectation_snapshot(db, payload)


@router.put("/expectations/{expectation_id}", response_model=ExpectationSnapshotOut)
def put_expectation_snapshot(
    expectation_id: int,
    payload: ExpectationSnapshotUpdate,
    db: Session = Depends(get_db),
) -> ExpectationSnapshotOut:
    row = db.get(ExpectationSnapshot, expectation_id)
    if not row:
        raise HTTPException(status_code=404, detail="Expectation snapshot not found")
    return update_expectation_snapshot(db, row, payload)


@router.get("/stocks/{code}/timeline", response_model=list[IntradayEvidenceEventOut])
def get_stock_timeline(code: str, db: Session = Depends(get_db)) -> list[IntradayEvidenceEventOut]:
    rows = (
        db.query(IntradayEvidenceEvent)
        .filter(IntradayEvidenceEvent.target_code.in_([code, code.lstrip("0")]))
        .order_by(IntradayEvidenceEvent.captured_at.desc())
        .limit(50)
        .all()
    )
    return [
        IntradayEvidenceEventOut(
            id=row.id,
            captured_at=row.captured_at,
            scope=row.scope,
            target_code=row.target_code,
            target_name=row.target_name,
            event_type=row.event_type,
            severity=row.severity,
            value=row.value,
            previous_value=row.previous_value,
            evidence=_json_list(row.evidence_json),
        )
        for row in rows
    ]


@router.get("/stocks/{code}/intraday-review", response_model=IntradayReviewOut)
def get_stock_intraday_review(code: str, db: Session = Depends(get_db)) -> IntradayReviewOut:
    holding = _find_holding_by_code(db, code)
    state = build_position_execution_state(db, holding) if holding else None
    if state is None:
        latest_state = (
            db.query(PositionExecutionState)
            .filter(PositionExecutionState.code.in_([code, code.lstrip("0")]))
            .order_by(PositionExecutionState.updated_at.desc(), PositionExecutionState.id.desc())
            .first()
        )
        if not latest_state:
            raise HTTPException(status_code=404, detail="No intraday review data found")
        timeline = get_stock_timeline(code, db)
        return IntradayReviewOut(
            code=latest_state.code,
            name=latest_state.name,
            generated_at=datetime.now(),
            latest_action=latest_state.recommended_action,
            latest_state=latest_state.state,
            data_quality=latest_state.data_quality,
            timeline=timeline,
            evidence=_json_list(latest_state.evidence_json),
            counter_evidence=_json_list(latest_state.counter_evidence_json),
            next_actions=_json_list(latest_state.invalid_conditions_json)[:3],
        )
    return IntradayReviewOut(
        code=state.code,
        name=state.name,
        generated_at=datetime.now(),
        latest_action=state.recommended_action,
        latest_state=state.state,
        data_quality=state.data_quality,
        timeline=state.events,
        evidence=state.evidence,
        counter_evidence=state.counter_evidence,
        next_actions=(state.invalid_conditions + state.recovery_conditions)[:5],
    )
