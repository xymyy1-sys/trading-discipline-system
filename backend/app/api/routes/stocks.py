from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta
import json

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
    minute_evidence_timeline,
)
from app.api.helpers.volume_price import build_volume_price_snapshot
from app.core.database import get_db
from app.models.trading import DataCaptureSnapshot, ExpectationRule, ExpectationSnapshot, Holding, IntradayEvidenceEvent, NextDayPlan, PositionExecutionState, VolumePriceSnapshot, WatchlistEntry
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
    WatchlistEntryIn,
    WatchlistEntryOut,
    ReplayReportOut,
)

router = APIRouter()


@router.get("/watchlist-recommendations", response_model=list[WatchlistRecommendationOut])
def watchlist_recommendations(db: Session = Depends(get_db)) -> list[WatchlistRecommendationOut]:
    from app.services.market_data import MarketDataProvider

    provider = MarketDataProvider()
    def previous_trading_days(count: int) -> list[str]:
        result: list[str] = []
        cursor = date.today()
        while len(result) < count:
            cursor -= timedelta(days=1)
            if cursor.weekday() < 5:
                result.append(cursor.isoformat())
        return result

    recent_dates = previous_trading_days(5)
    def load_ladder(value: str):
        try:
            return provider.limit_up_ladder(value)
        except TypeError:
            # 兼容测试替身及只实现默认日期的旧数据提供器。
            return provider.limit_up_ladder()
    executor = ThreadPoolExecutor(max_workers=8)
    theme_future = executor.submit(provider.theme_radar)
    ladder_future = executor.submit(load_ladder, recent_dates[0])
    historical_futures = {value: executor.submit(load_ladder, value) for value in recent_dates[1:]}
    broken_future = executor.submit(provider.broken_limit_pool, recent_dates[0])
    all_futures = (theme_future, ladder_future, broken_future, *historical_futures.values())
    done, pending = wait(all_futures, timeout=22)
    try:
        radar = theme_future.result() if theme_future in done else None
    except Exception:
        radar = None
    try:
        ladder = ladder_future.result() if ladder_future in done else None
    except Exception:
        ladder = None
    historical_ladders = []
    for value, future in historical_futures.items():
        if future not in done:
            continue
        try:
            historical_ladders.append((value, future.result()))
        except Exception:
            continue
    try:
        broken_stocks = broken_future.result() if broken_future in done else []
    except Exception:
        broken_stocks = []
    for future in pending:
        future.cancel()
    executor.shutdown(wait=False, cancel_futures=True)
    if radar is None and ladder is None:
        raise HTTPException(status_code=503, detail="主线题材与涨停行情源暂不可用，请稍后重试；已有观察池和持仓数据未受影响")

    holding_codes = {row.code for row in db.query(Holding.code).all()}
    overrides = {row.code: row for row in db.query(WatchlistEntry).all()}
    rows: dict[str, dict] = {}
    theme_source = radar.source if radar else "题材数据不可用"
    for theme in (radar.themes[:20] if radar else []):
        for stock in theme.core_stocks:
            if not stock.code:
                continue
            row = rows.setdefault(stock.code, {
                "code": stock.code, "name": stock.name, "theme_score": 0, "limit_score": 0, "theme": theme.name,
                "role": stock.role, "limit_level": 0, "limit_quality": "未进入涨停梯队",
                "fund_signal": "", "current_price": 0.0, "sealed_amount": 0.0, "turnover": 0.0,
                "break_count": 0, "first_limit_time": "", "last_limit_time": "",
                "reasons": [], "risks": [], "sources": set(),
            })
            theme_points = min(45, round(theme.score * 0.45))
            rank_points = max(0, 12 - theme.rank)
            role_points = 12 if any(word in stock.role for word in ("龙头", "核心", "前排")) else 5
            candidate_theme_score = theme_points + rank_points + role_points + (8 if theme.net_inflow > 0 else 0)
            if candidate_theme_score <= row["theme_score"]:
                row["sources"].add(theme_source)
                continue
            row["theme_score"] = candidate_theme_score
            row["theme"] = theme.name
            row["role"] = stock.role
            row["reasons"] = [
                f"{theme.name}题材排名第{theme.rank}，强度{theme.score}分",
                f"题材角色：{stock.role or '核心股'}",
            ]
            row["risks"] = [risk for risk in row["risks"] if "题材资金" not in risk]
            if theme.net_inflow > 0:
                row["fund_signal"] = f"题材净流入{theme.net_inflow:.2f}亿"
                row["reasons"].append(row["fund_signal"])
            else:
                row["fund_signal"] = ""
                row["risks"].append("题材资金尚未形成净流入确认")
            row["sources"].add(theme_source)

    ladder_source = ladder.source if ladder else "涨停数据不可用"
    for group in (ladder.groups if ladder else []):
        for stock in group.stocks:
            if not stock.code:
                continue
            row = rows.setdefault(stock.code, {
                "code": stock.code, "name": stock.name, "theme_score": 0, "limit_score": 0,
                "theme": (stock.concepts[0] if stock.concepts else stock.industry), "role": "涨停前排",
                "limit_level": 0, "limit_quality": "", "fund_signal": "",
                "current_price": stock.price, "sealed_amount": 0.0, "turnover": 0.0,
                "break_count": 0, "first_limit_time": "", "last_limit_time": "",
                "reasons": [], "risks": [], "sources": set(),
            })
            row["current_price"] = stock.price or row["current_price"]
            row["limit_level"] = max(row["limit_level"], stock.consecutive_limit_days, group.level)
            row["sealed_amount"] = max(row["sealed_amount"], float(stock.sealed_amount or 0))
            row["turnover"] = float(stock.turnover or 0)
            row["break_count"] = int(stock.break_count or 0)
            row["first_limit_time"] = stock.first_limit_time
            row["last_limit_time"] = stock.last_limit_time
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
            row["limit_score"] = max(row["limit_score"], quality_score)
            row["reasons"].append(f"{group.label}，{row['limit_quality']}")
            row["category"] = "昨日涨停承接观察"
            row["sources"].add(ladder_source)

    for history_date, history_ladder in historical_ladders:
        days_ago = recent_dates.index(history_date) + 1 if history_date in recent_dates else 2
        for group in history_ladder.groups:
            for stock in group.stocks:
                if not stock.code or stock.code in rows and rows[stock.code].get("limit_level", 0) > 0:
                    continue
                row = rows.setdefault(stock.code, {
                    "code": stock.code, "name": stock.name, "theme_score": 0, "limit_score": 0,
                    "theme": (stock.concepts[0] if stock.concepts else stock.industry), "role": "近期涨停结构观察",
                    "limit_level": 0, "limit_quality": "近期涨停后承接待验证", "fund_signal": "",
                    "current_price": stock.price, "sealed_amount": 0.0, "turnover": stock.turnover,
                    "break_count": stock.break_count, "first_limit_time": "", "last_limit_time": "",
                    "reasons": [], "risks": [], "sources": set(),
                })
                row["limit_score"] = max(row["limit_score"], max(8, 24 - days_ago * 4 + group.level * 2))
                row["theme_score"] = max(row["theme_score"], 38)
                row["category"] = "近几日涨停／炸板承接观察"
                row["reasons"].append(f"{history_date} 曾涨停，观察横盘支撑、回踩承接及再次选择方向")
                row["sources"].add(history_ladder.source)

    for stock in broken_stocks:
        if not stock.code or stock.code in rows and rows[stock.code].get("limit_level", 0) > 0:
            continue
        row = rows.setdefault(stock.code, {
            "code": stock.code, "name": stock.name, "theme_score": 38, "limit_score": 22,
            "theme": (stock.concepts[0] if stock.concepts else stock.industry), "role": "炸板承接观察",
            "limit_level": 0, "limit_quality": f"冲板未封，炸板{stock.break_count}次", "fund_signal": "",
            "current_price": stock.price, "sealed_amount": 0.0, "turnover": stock.turnover,
            "break_count": stock.break_count, "first_limit_time": stock.first_limit_time, "last_limit_time": "",
            "reasons": [], "risks": [], "sources": set(),
        })
        row["category"] = "近几日涨停／炸板承接观察"
        row["reasons"].append(f"昨日曾冲击涨停但未封住，当前涨幅{stock.change_pct:+.2f}%，重点验证炸板后的支撑与承接")
        row["sources"].add("东方财富炸板池")

    codes = list(rows)
    for entry in overrides.values():
        if entry.status == "active" and entry.code not in rows:
            rows[entry.code] = {
                "code": entry.code, "name": entry.name or entry.code, "theme_score": 70, "limit_score": 15,
                "theme": "手动观察", "role": "用户加入", "limit_level": 0, "limit_quality": "等待行情验证",
                "fund_signal": "", "current_price": 0.0, "sealed_amount": 0.0, "turnover": 0.0,
                "break_count": 0, "first_limit_time": "", "last_limit_time": "",
                "reasons": ["用户手动加入观察池"], "risks": [], "sources": {"用户维护"}, "category": "手动自选",
            }
    codes = list(rows)
    expectations: dict[str, ExpectationSnapshot] = {}
    volumes: dict[str, VolumePriceSnapshot] = {}
    plans: dict[str, NextDayPlan] = {}
    if codes:
        for item in db.query(ExpectationSnapshot).filter(ExpectationSnapshot.code.in_(codes)).order_by(ExpectationSnapshot.created_at.desc()).all():
            expectations.setdefault(item.code, item)
        for item in db.query(VolumePriceSnapshot).filter(VolumePriceSnapshot.code.in_(codes)).order_by(VolumePriceSnapshot.captured_at.desc()).all():
            volumes.setdefault(item.code, item)
        for item in db.query(NextDayPlan).filter(NextDayPlan.code.in_(codes)).order_by(NextDayPlan.updated_at.desc()).all():
            plans.setdefault(item.code, item)

    outputs: list[WatchlistRecommendationOut] = []
    for row in rows.values():
        if overrides.get(row["code"]) and overrides[row["code"]].status == "excluded":
            continue
        if overrides.get(row["code"]) and overrides[row["code"]].status == "active" and overrides[row["code"]].source == "manual":
            row["category"] = "手动自选"
            row["reasons"].insert(0, "用户手动加入观察池")
        score_value = row["theme_score"] + row["limit_score"]
        expectation = expectations.get(row["code"])
        volume = volumes.get(row["code"])
        plan = plans.get(row["code"])
        if row.get("category") == "近几日涨停／炸板承接观察" and volume:
            support = max(float(volume.ma20 or 0), float(volume.vwap or 0))
            if support > 0 and float(volume.price or 0) >= support * 0.98:
                score_value += 10
                row["reasons"].append(f"现价仍在重要支撑{support:.2f}附近或上方，尚未有效跌破")
            elif support > 0:
                score_value -= 18
                row["risks"].append(f"现价已跌破重要支撑{support:.2f}，暂不作为横盘承接候选")
            if volume.high_drawdown <= 4 and volume.price_vs_vwap >= -1.5:
                score_value += 8
                row["reasons"].append("回撤受控且未明显远离分时均价，炸板/涨停后仍有承接")
        missing_conditions: list[str] = []
        inferred_expectation, inferred_gap, inference_reasons = _infer_limit_up_expectation(row)
        row["reasons"].extend(inference_reasons)
        expectation_ok = True
        # 盘前观察池使用当日收盘封板事实推演次日预期；次日开盘后再由预期快照验证预期差。
        expectation_status = inferred_expectation
        expectation_gap = inferred_gap
        if expectation and expectation.trade_date > str(getattr(ladder, "trade_date", "") or ""):
            expectation_status = _candidate_state_label(expectation.expectation_result)
            expectation_gap = round(expectation.expectation_gap_score, 2)
            expectation_ok = expectation.expectation_result in {"STRONGER", "MATCHED"} and expectation.expectation_gap_score >= 0
            if not expectation_ok:
                missing_conditions.append("次日验证后的预期差为负")
        limit_volume_confirmed = row["break_count"] <= 1 and 0 < row["turnover"] <= 30 and row["sealed_amount"] > 0
        volume_ok = bool(volume and volume.vwap_reliable and volume.data_quality not in {"missing", "degraded"}) or limit_volume_confirmed
        if not volume_ok:
            missing_conditions.append("封板量价质量未确认")
        current_price = float(row["current_price"] or (plan.current_price if plan else 0) or 0)
        target_price = float((plan.trim_price or plan.limit_up_price or plan.confirm_price) if plan else 0)
        stop_price = float((plan.final_risk_price or plan.reduce_price) if plan else 0)
        risk_reward_ratio = None
        if current_price > stop_price > 0 and target_price > current_price:
            risk_reward_ratio = round((target_price - current_price) / (current_price - stop_price), 2)
        elif current_price > 0:
            target_pct = 0.06 if row["limit_level"] >= 2 and row["break_count"] == 0 else 0.05 if row["break_count"] <= 1 else 0.03
            invalidation_pct = 0.03 if row["sealed_amount"] >= 0.5 else 0.035
            risk_reward_ratio = round(target_pct / invalidation_pct, 2)
            row["reasons"].append(f"系统按次日目标空间{target_pct:.1%}/失效幅度{invalidation_pct:.1%}推演风险收益比")
        risk_reward_ok = risk_reward_ratio is not None and risk_reward_ratio >= 1.5
        if not risk_reward_ok:
            missing_conditions.append("风险收益比未达到1.5")
        gate_passed = expectation_ok and volume_ok and risk_reward_ok
        if "ST" in row["name"].upper():
            score_value -= 50
            row["risks"].append("风险警示股票不纳入自动观察池")
        if row["code"] in holding_codes:
            score_value -= 15
            row["risks"].append("当前已持仓，应转入持仓执行而非新增观察")
        score = max(0, min(100, int(score_value)))
        tier = "重点观察" if score >= 70 and gate_passed and not any("风险警示" in risk for risk in row["risks"]) else "等待确认" if score >= 50 else "暂不纳入"
        outputs.append(WatchlistRecommendationOut(
            code=row["code"], name=row["name"], score=score, tier=tier,
            theme=row["theme"], role=row["role"], limit_level=row["limit_level"],
            limit_quality=row["limit_quality"], fund_signal=row["fund_signal"],
            expectation_status=expectation_status,
            volume_price_status=_candidate_state_label(volume.pattern) if volume and volume.vwap_reliable else (f"封板量价确认：封单{row['sealed_amount']:.2f}亿，换手{row['turnover']:.1f}%" if limit_volume_confirmed else "封板量价待确认"),
            expectation_gap=expectation_gap,
            risk_reward_ratio=risk_reward_ratio, gate_passed=gate_passed,
            missing_conditions=missing_conditions,
            reasons=list(dict.fromkeys(row["reasons"])), risks=list(dict.fromkeys(row["risks"])),
            source=" + ".join(sorted(row["sources"])),
            category=str(row.get("category") or "主线题材观察"),
            updated_at=max([value for value in (radar.updated_at if radar else None, ladder.updated_at if ladder else None) if value is not None], default=None),
        ))
    output_by_code = {item.code: item for item in outputs}
    snapshot_date = str(getattr(ladder, "trade_date", "") or date.today().isoformat())
    today_auto = db.query(WatchlistEntry).filter(
        WatchlistEntry.source == "auto",
        WatchlistEntry.snapshot_date == snapshot_date,
    ).all()
    if not today_auto:
        ranked = [item for item in sorted(outputs, key=lambda item: (-item.score, item.code)) if item.category in {"昨日涨停承接观察", "近几日涨停／炸板承接观察"}]
        first = [item for item in ranked if item.category == "昨日涨停承接观察"][:5]
        second = [item for item in ranked if item.category == "近几日涨停／炸板承接观察"][:5]
        selected = (first + second)[:10]
        if len(selected) < 10:
            selected_codes = {item.code for item in selected}
            selected.extend(item for item in ranked if item.code not in selected_codes and item not in selected)
            selected = selected[:10]
        for rank, item in enumerate(selected, start=1):
            existing = overrides.get(item.code)
            if existing and existing.status == "excluded":
                continue
            if existing is None:
                existing = WatchlistEntry(code=item.code, name=item.name, source="auto")
            if existing.source != "manual":
                existing.source = "auto"
                existing.status = "active"
                existing.snapshot_date = snapshot_date
                existing.category = item.category
                existing.snapshot_rank = rank
                db.add(existing)
        db.commit()
        today_auto = db.query(WatchlistEntry).filter(WatchlistEntry.source == "auto", WatchlistEntry.snapshot_date == snapshot_date).all()

    active_entries = db.query(WatchlistEntry).filter(WatchlistEntry.status == "active").all()
    active_codes = {item.code for item in active_entries if item.source == "manual" or item.snapshot_date == snapshot_date}
    return sorted(
        [output_by_code[code] for code in active_codes if code in output_by_code],
        key=lambda item: next((entry.snapshot_rank for entry in active_entries if entry.code == item.code and entry.snapshot_rank), 999),
    )


@router.post("/watchlist", response_model=WatchlistEntryOut)
def add_watchlist_entry(payload: WatchlistEntryIn, db: Session = Depends(get_db)) -> WatchlistEntryOut:
    code = payload.code.strip()
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(status_code=422, detail="请输入6位股票代码")
    resolved_name = payload.name.strip()
    if not resolved_name:
        try:
            resolved_name = str(quote_for_code(code).get("name") or "").strip()
        except Exception:
            resolved_name = ""
    row = db.query(WatchlistEntry).filter(WatchlistEntry.code == code).first()
    if row is None:
        row = WatchlistEntry(code=code, name=resolved_name or code, status="active", source="manual", category="手动自选")
    else:
        row.name = resolved_name or row.name or code
        row.status = "active"
        row.source = "manual"
        row.category = "手动自选"
        row.updated_at = datetime.now()
    db.add(row); db.commit(); db.refresh(row)
    return WatchlistEntryOut(code=row.code, name=row.name, status=row.status, source=row.source)


@router.delete("/watchlist/{code}", response_model=WatchlistEntryOut)
def exclude_watchlist_entry(code: str, db: Session = Depends(get_db)) -> WatchlistEntryOut:
    row = db.query(WatchlistEntry).filter(WatchlistEntry.code == code).first()
    if row is None:
        row = WatchlistEntry(code=code, name=code, status="excluded", source="manual")
    else:
        row.status = "excluded"; row.updated_at = datetime.now()
    db.add(row); db.commit(); db.refresh(row)
    return WatchlistEntryOut(code=row.code, name=row.name, status=row.status, source=row.source)


def _infer_limit_up_expectation(row: dict) -> tuple[str, float | None, list[str]]:
    level = int(row.get("limit_level") or 1)
    sealed = float(row.get("sealed_amount") or 0)
    breaks = int(row.get("break_count") or 0)
    turnover = float(row.get("turnover") or 0)
    score = 50 + min(24, level * 6) + min(12, sealed * 4) - min(24, breaks * 8)
    if 3 <= turnover <= 22:
        score += 8
    elif turnover > 30:
        score -= 10
    grade = "强预期" if score >= 78 else "中强预期" if score >= 65 else "中性预期" if score >= 52 else "弱预期"
    gap = round((score - 60) / 5, 1)
    evidence = [
        f"系统盘后推演：{level}板、封单{sealed:.2f}亿、炸板{breaks}次、换手{turnover:.1f}%",
        f"次日基础预期：{grade}；开盘后再用竞价、量价和承接验证动态预期差",
    ]
    return f"系统推演·{grade}", gap, evidence


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
        "VWAP_BREAKDOWN": "跌破分时均价", "ABOVE_VWAP": "站上分时均价",
        "VOLUME_PRICE_WEAKENING": "量价转弱", "HIGH_VOLUME_STAGNATION": "高位放量滞涨",
        "HEALTHY": "量价健康", "NEUTRAL": "量价中性",
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
    latest_state = (
            db.query(PositionExecutionState)
            .filter(PositionExecutionState.code.in_([code, code.lstrip("0")]))
            .order_by(PositionExecutionState.updated_at.desc(), PositionExecutionState.id.desc())
            .first()
    )
    capture = (
        db.query(DataCaptureSnapshot)
        .filter(DataCaptureSnapshot.target_code.in_([code, code.lstrip("0")]), DataCaptureSnapshot.data_type == "stock_minute")
        .order_by(DataCaptureSnapshot.captured_at.desc(), DataCaptureSnapshot.id.desc())
        .first()
    )
    quote: dict = {}
    if capture:
        try:
            quote = json.loads(capture.raw_value_json or "{}")
        except Exception:
            quote = {}
    timeline = minute_evidence_timeline(code, holding.name if holding else (latest_state.name if latest_state else code), quote)
    if not timeline:
        timeline = get_stock_timeline(code, db)
    if latest_state:
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
    state = build_position_execution_state(db, holding, quote=quote, persist=False) if holding else None
    if state is None:
        raise HTTPException(status_code=404, detail="No intraday review data found")
    return IntradayReviewOut(
        code=state.code,
        name=state.name,
        generated_at=datetime.now(),
        latest_action=state.recommended_action,
        latest_state=state.state,
        data_quality=state.data_quality,
        timeline=timeline or state.events,
        evidence=state.evidence,
        counter_evidence=state.counter_evidence,
        next_actions=(state.invalid_conditions + state.recovery_conditions)[:5],
    )
