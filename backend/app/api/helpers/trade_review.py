import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models.trading import (
    ActionRecommendation,
    CalibrationRun,
    ExpectationRule,
    ExpectationSnapshot,
    NextDayPlan,
    RecommendationFeedback,
    TTradePlan,
    TradeLog,
    TradeReview,
    VolumePriceSnapshot,
)
from app.schemas.trading import (
    CalibrationMetricOut,
    CalibrationProposalOut,
    CalibrationRuleChangeOut,
    CalibrationRunOut,
    CalibrationIssueOut,
    CalibrationSuggestionOut,
    FeedbackSummaryOut,
    PlanDeviationOut,
    ReviewCalibrationSummaryOut,
    TradeLogOut,
    TradeReviewOut,
)
from app.services.market_data import _get_response_cache, _last_trading_day
from app.api.helpers.quotes import (
    _latest_a_share_quotes,
    _quote_lookup_code,
    _is_realtime_note,
    _safe_float,
    _normalize_code
)

def _json_obj(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}

def _json_list(raw: str) -> list[str]:
    try:
        value = json.loads(raw or "[]")
        return [str(item) for item in value] if isinstance(value, list) else []
    except Exception:
        return []

def _trade_out(trade: TradeLog, review: TradeReview | None = None) -> TradeLogOut:
    data = trade.__dict__.copy()
    data.pop("_sa_instance_state", None)
    raw_tags = str(data.pop("human_tags", "") or "")
    return TradeLogOut(
        **data,
        human_tags=[tag for tag in raw_tags.split(",") if tag],
        review=_trade_review_out(review) if review else None,
    )

def _trade_review_out(review: TradeReview) -> TradeReviewOut:
    return TradeReviewOut(
        id=review.id,
        trade_id=review.trade_id,
        code=review.code,
        name=review.name,
        verdict=review.verdict,
        status=review.status,
        discipline_score=review.discipline_score,
        summary=review.summary,
        stock_context=review.stock_context,
        sector_context=review.sector_context,
        market_context=review.market_context,
        error_message=review.error_message,
        mistakes=_json_list(review.mistakes),
        avoid_actions=_json_list(review.avoid_actions),
        weakness_tags=_json_list(review.weakness_tags),
        created_at=review.created_at,
    )


def _review_calibration_summary(db: Session) -> ReviewCalibrationSummaryOut:
    trades = db.query(TradeLog).order_by(TradeLog.traded_at.desc()).limit(100).all()
    reviews = db.query(TradeReview).order_by(TradeReview.created_at.desc()).limit(100).all()
    plans = db.query(NextDayPlan).order_by(NextDayPlan.plan_date.desc(), NextDayPlan.updated_at.desc()).limit(100).all()
    feedback = db.query(RecommendationFeedback).order_by(RecommendationFeedback.created_at.desc()).limit(100).all()
    feedback_by_recommendation = {item.recommendation_id: item for item in feedback}
    recommendations = db.query(ActionRecommendation).order_by(ActionRecommendation.created_at.desc()).limit(100).all()

    done_reviews = [item for item in reviews if item.status == "done"]
    pending_review_count = sum(1 for item in reviews if item.status in {"pending", "failed"})
    avg_score = round(sum(item.discipline_score for item in done_reviews) / len(done_reviews)) if done_reviews else 0
    plan_reviews = [item for item in plans if item.review_expectation or item.review_execution or item.review_deviation]
    missing_plan_reviews = [item for item in plans if not (item.review_expectation or item.review_execution or item.review_deviation)]
    feedback_counter: Counter[str] = Counter(item.status for item in feedback)
    ignored_recommendation_count = sum(
        1
        for item in recommendations
        if feedback_by_recommendation.get(item.id)
        and feedback_by_recommendation[item.id].status in {"忽略", "暂不执行"}
    )
    model_metrics, calibration_suggestions = _model_calibration_metrics(db, plan_reviews, feedback, recommendations)
    issues: list[CalibrationIssueOut] = []

    if pending_review_count:
        issues.append(CalibrationIssueOut(
            level="中",
            title="交易复盘未完成",
            detail=f"仍有 {pending_review_count} 条复盘处于生成中或失败状态。",
            action="先刷新交易日志，失败项重新保存触发复盘。",
        ))
    if missing_plan_reviews:
        sample = "、".join(item.name for item in missing_plan_reviews[:3])
        issues.append(CalibrationIssueOut(
            level="高" if len(missing_plan_reviews) >= 3 else "中",
            title="次日计划缺少盘后校准",
            detail=f"{len(missing_plan_reviews)} 张计划卡未填写预期/执行/偏差复盘。{sample}",
            action="盘后至少补齐预期是否命中、实际执行、偏差原因。",
        ))
    low_score_reviews = [item for item in done_reviews if item.discipline_score < 60]
    if low_score_reviews:
        item = low_score_reviews[0]
        issues.append(CalibrationIssueOut(
            level="高",
            title="纪律评分低于 60",
            detail=f"{item.name}：{item.summary}",
            action="下一笔交易前先复核该错误是否再次出现。",
            code=item.code,
            name=item.name,
        ))
    if ignored_recommendation_count:
        issues.append(CalibrationIssueOut(
            level="中",
            title="执行提醒被忽略",
            detail=f"{ignored_recommendation_count} 条状态机建议被忽略或暂不执行。",
            action="复盘是否因为主观判断覆盖了系统证据。",
        ))

    deviations: list[PlanDeviationOut] = []
    for plan in plan_reviews[:20]:
        joined = f"{plan.review_expectation} {plan.review_execution} {plan.review_deviation}"
        severity = "高" if _contains_any(joined, ("严重", "未执行", "幻想", "补仓", "亏损", "破位")) else "中" if plan.review_deviation else "观察"
        deviations.append(PlanDeviationOut(
            plan_id=plan.id,
            code=plan.code,
            name=plan.name,
            plan_date=plan.plan_date,
            expectation=plan.review_expectation,
            execution=plan.review_execution,
            deviation=plan.review_deviation,
            severity=severity,
        ))

    next_actions = [
        "每张次日计划盘后必须填写：预期是否命中、是否按计划执行、偏差原因。",
        "低于 60 分的交易，下一笔买入前先检查同类错误是否重复。",
        "被忽略的执行提醒必须写明理由，否则视为纪律风险样本。",
    ]
    if not issues:
        next_actions.insert(0, "当前 P1 复盘闭环没有明显缺口，继续积累样本。")
    focus = "先补齐计划盘后校准，再处理低分交易和被忽略提醒。"
    if low_score_reviews:
        focus = f"优先复盘低分交易：{low_score_reviews[0].name}。"
    elif missing_plan_reviews:
        focus = "优先补齐次日计划盘后校准。"
    elif ignored_recommendation_count:
        focus = "优先解释被忽略/暂不执行的系统建议。"

    return ReviewCalibrationSummaryOut(
        trade_count=len(trades),
        review_count=len(reviews),
        plan_review_count=len(plan_reviews),
        missing_plan_review_count=len(missing_plan_reviews),
        execution_feedback_count=len(feedback),
        ignored_recommendation_count=ignored_recommendation_count,
        pending_review_count=pending_review_count,
        avg_discipline_score=avg_score,
        focus=focus,
        issues=issues,
        recent_plan_deviations=deviations,
        feedback_summary=[FeedbackSummaryOut(status=status, count=count) for status, count in feedback_counter.most_common()],
        model_metrics=model_metrics,
        calibration_suggestions=calibration_suggestions,
        next_actions=next_actions,
    )


def _model_calibration_metrics(
    db: Session,
    plan_reviews: list[NextDayPlan],
    feedback: list[RecommendationFeedback],
    recommendations: list[ActionRecommendation],
) -> tuple[list[CalibrationMetricOut], list[CalibrationSuggestionOut]]:
    expectations = (
        db.query(ExpectationSnapshot)
        .order_by(ExpectationSnapshot.created_at.desc())
        .limit(200)
        .all()
    )
    volume_snapshots = (
        db.query(VolumePriceSnapshot)
        .order_by(VolumePriceSnapshot.captured_at.desc())
        .limit(200)
        .all()
    )
    t_plans = db.query(TTradePlan).order_by(TTradePlan.updated_at.desc()).limit(200).all()

    metrics: list[CalibrationMetricOut] = []
    suggestions: list[CalibrationSuggestionOut] = []

    expectation_samples = [item for item in expectations if item.expectation_result and item.expectation_result != "UNKNOWN"]
    expectation_fail = sum(1 for item in expectation_samples if item.expectation_result in {"WEAKER", "INVALID", "SLIGHTLY_WEAKER"})
    expectation_success = sum(1 for item in expectation_samples if item.expectation_result in {"MATCHED", "STRONGER"})
    metrics.append(_metric(
        key="expectation_hit",
        label="阶段预期命中",
        sample_count=len(expectation_samples),
        success_count=expectation_success,
        fail_count=expectation_fail,
        evidence=[
            f"弱于预期 {expectation_fail} 次",
            f"符合/强于预期 {expectation_success} 次",
        ],
    ))
    if len(expectation_samples) >= 5 and expectation_fail / len(expectation_samples) >= 0.45:
        suggestions.append(CalibrationSuggestionOut(
            level="高",
            target="预期阈值",
            suggestion="收紧强预期开盘和五分钟确认阈值，弱于预期时默认降一档仓位。",
            reason="阶段预期样本中弱于预期比例偏高，说明当前预期设定可能过宽或确认过晚。",
            sample_count=len(expectation_samples),
        ))

    weak_volume_patterns = ("跌破VWAP", "冲高回落", "放量下跌", "量价转弱", "缩量冲高")
    volume_samples = [item for item in volume_snapshots if item.pattern]
    weak_volume = [item for item in volume_samples if _contains_any(item.pattern, weak_volume_patterns)]
    repaired_volume = [item for item in volume_samples if _contains_any(item.pattern, ("修复", "站上VWAP", "放量上涨"))]
    metrics.append(_metric(
        key="volume_price_risk",
        label="量价风险识别",
        sample_count=len(volume_samples),
        success_count=len(weak_volume),
        fail_count=len(repaired_volume),
        evidence=[
            f"风险形态 {len(weak_volume)} 次",
            f"修复/强势形态 {len(repaired_volume)} 次",
        ],
        success_word="风险样本",
    ))
    if len(volume_samples) >= 8 and len(weak_volume) / len(volume_samples) >= 0.5:
        suggestions.append(CalibrationSuggestionOut(
            level="中",
            target="量价引擎",
            suggestion="把跌破 VWAP 后的补仓/做T限制继续前置，要求修复确认后再恢复动作。",
            reason="近期量价风险形态占比偏高，执行端应优先防止越跌越补。",
            sample_count=len(volume_samples),
        ))

    completed_t = [item for item in t_plans if item.status in {"done", "completed", "已完成"} or item.cost_reduction]
    positive_t = [item for item in completed_t if item.cost_reduction > 0]
    avg_cost_reduction = (
        round(sum(item.cost_reduction for item in completed_t) / len(completed_t), 4)
        if completed_t else 0
    )
    metrics.append(_metric(
        key="t_trade_effect",
        label="做T真实贡献",
        sample_count=len(completed_t),
        success_count=len(positive_t),
        fail_count=max(0, len(completed_t) - len(positive_t)),
        average_value=avg_cost_reduction,
        evidence=[f"平均降本 {avg_cost_reduction:.4f}", f"正贡献 {len(positive_t)} 次"],
    ))
    if len(completed_t) >= 3 and len(positive_t) / len(completed_t) < 0.5:
        suggestions.append(CalibrationSuggestionOut(
            level="高",
            target="做T策略",
            suggestion="暂停扩大做T比例，只保留盈利趋势仓小比例正T。",
            reason="已完成做T计划的正贡献比例不足，说明做T对账户净贡献不稳定。",
            sample_count=len(completed_t),
        ))

    feedback_by_recommendation = {item.recommendation_id: item for item in feedback}
    feedback_samples = [item for item in recommendations if item.id in feedback_by_recommendation]
    executed = [
        item for item in feedback_samples
        if feedback_by_recommendation[item.id].status in {"已执行", "部分执行"}
    ]
    ignored = [
        item for item in feedback_samples
        if feedback_by_recommendation[item.id].status in {"忽略", "暂不执行"}
    ]
    metrics.append(_metric(
        key="execution_adoption",
        label="执行建议采纳",
        sample_count=len(feedback_samples),
        success_count=len(executed),
        fail_count=len(ignored),
        evidence=[f"已执行/部分执行 {len(executed)} 条", f"忽略/暂不执行 {len(ignored)} 条"],
    ))
    if len(feedback_samples) >= 5 and len(ignored) / len(feedback_samples) >= 0.4:
        suggestions.append(CalibrationSuggestionOut(
            level="中",
            target="执行提醒",
            suggestion="被忽略建议必须填写理由；连续忽略同类风险提醒时，在首页提高风险等级。",
            reason="系统建议存在较高比例未执行，后续需要区分规则误报与主观覆盖纪律。",
            sample_count=len(feedback_samples),
        ))

    severe_deviations = [
        item for item in plan_reviews
        if _contains_any(f"{item.review_expectation} {item.review_execution} {item.review_deviation}", ("严重", "未执行", "幻想", "破位", "补仓", "亏损"))
    ]
    metrics.append(_metric(
        key="plan_execution_drift",
        label="计划执行偏差",
        sample_count=len(plan_reviews),
        success_count=max(0, len(plan_reviews) - len(severe_deviations)),
        fail_count=len(severe_deviations),
        evidence=[f"严重偏差 {len(severe_deviations)} 张", f"已复盘计划 {len(plan_reviews)} 张"],
    ))
    if len(plan_reviews) >= 5 and len(severe_deviations) / len(plan_reviews) >= 0.35:
        suggestions.append(CalibrationSuggestionOut(
            level="高",
            target="计划纪律",
            suggestion="次日计划增加盘中强制检查点，弱于预期和破位不得等到收盘才处理。",
            reason="计划复盘中的严重执行偏差占比偏高。",
            sample_count=len(plan_reviews),
        ))

    if not suggestions:
        suggestions.append(CalibrationSuggestionOut(
            level="观察",
            target="样本积累",
            suggestion="继续积累预期、量价、做T和执行反馈样本，暂不自动调整参数。",
            reason="当前样本量或偏差强度不足以支撑自动校准。",
            sample_count=sum(item.sample_count for item in metrics),
        ))

    return metrics, suggestions


def _expectation_calibration_proposal(db: Session) -> CalibrationProposalOut:
    summary = _review_calibration_summary(db)
    metric = next(item for item in summary.model_metrics if item.key == "expectation_hit")
    active_run = (
        db.query(CalibrationRun)
        .filter(CalibrationRun.metric_key == "expectation_hit", CalibrationRun.status == "applied")
        .order_by(CalibrationRun.created_at.desc())
        .first()
    )
    already_applied = bool(active_run and active_run.sample_count >= metric.sample_count)
    eligible = metric.sample_count >= 20 and metric.fail_count / max(metric.sample_count, 1) >= 0.45 and not already_applied
    rationale = (
        f"同一批 {metric.sample_count} 个样本的校准已经应用；需要新增有效样本或先回滚，禁止重复累加阈值。"
        if already_applied else (
        f"{metric.sample_count} 个有效阶段预期样本中有 {metric.fail_count} 个弱于预期；"
        "建议把弱于预期识别线和强于预期确认线各收紧 0.25 个百分点。"
        if eligible else
        f"当前有效样本 {metric.sample_count}/20，或弱于预期比例未达到 45%，不允许应用参数变更。"
        )
    )
    changes: list[CalibrationRuleChangeOut] = []
    if eligible:
        for rule in db.query(ExpectationRule).filter(ExpectationRule.enabled.is_(True)).order_by(ExpectationRule.id).all():
            changes.extend([
                CalibrationRuleChangeOut(
                    rule_id=rule.id, display_name=rule.display_name,
                    field="underperform_threshold", before=rule.underperform_threshold,
                    after=round(min(rule.underperform_threshold + 0.25, rule.expected_open_low - 0.01), 2),
                ),
                CalibrationRuleChangeOut(
                    rule_id=rule.id, display_name=rule.display_name,
                    field="outperform_threshold", before=rule.outperform_threshold,
                    after=round(max(rule.outperform_threshold + 0.25, rule.expected_open_high + 0.01), 2),
                ),
            ])
    return CalibrationProposalOut(
        sample_count=metric.sample_count,
        eligible=eligible,
        rationale=rationale,
        changes=changes,
    )


def _apply_expectation_calibration(db: Session, confirmation: str) -> CalibrationRunOut:
    if confirmation != "APPLY_CALIBRATION":
        raise ValueError("confirmation must be APPLY_CALIBRATION")
    proposal = _expectation_calibration_proposal(db)
    if not proposal.eligible or not proposal.changes:
        raise ValueError("calibration sample gate is not satisfied")
    rule_ids = sorted({change.rule_id for change in proposal.changes})
    rules = db.query(ExpectationRule).filter(ExpectationRule.id.in_(rule_ids)).all()
    before = [_rule_calibration_state(rule) for rule in rules]
    rules_by_id = {rule.id: rule for rule in rules}
    for change in proposal.changes:
        setattr(rules_by_id[change.rule_id], change.field, change.after)
    after = [_rule_calibration_state(rule) for rule in rules]
    run = CalibrationRun(
        metric_key=proposal.metric_key,
        sample_count=proposal.sample_count,
        status="applied",
        rationale=proposal.rationale,
        before_json=json.dumps(before, ensure_ascii=False),
        after_json=json.dumps(after, ensure_ascii=False),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return _calibration_run_out(run)


def _rollback_calibration_run(db: Session, run_id: int) -> CalibrationRunOut:
    run = db.query(CalibrationRun).filter(CalibrationRun.id == run_id).first()
    if run is None:
        raise LookupError("calibration run not found")
    if run.status != "applied":
        raise ValueError("calibration run is not active")
    before = json.loads(run.before_json or "[]")
    for state in before:
        rule = db.query(ExpectationRule).filter(ExpectationRule.id == int(state["id"])).first()
        if rule:
            for field in ("underperform_threshold", "outperform_threshold"):
                setattr(rule, field, float(state[field]))
    run.status = "rolled_back"
    run.rolled_back_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return _calibration_run_out(run)


def _rule_calibration_state(rule: ExpectationRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "display_name": rule.display_name,
        "underperform_threshold": rule.underperform_threshold,
        "outperform_threshold": rule.outperform_threshold,
    }


def _calibration_run_out(run: CalibrationRun) -> CalibrationRunOut:
    before = {int(item["id"]): item for item in json.loads(run.before_json or "[]")}
    after = {int(item["id"]): item for item in json.loads(run.after_json or "[]")}
    changes: list[CalibrationRuleChangeOut] = []
    for rule_id, old in before.items():
        new = after.get(rule_id, old)
        for field in ("underperform_threshold", "outperform_threshold"):
            changes.append(CalibrationRuleChangeOut(
                rule_id=rule_id,
                display_name=str(old.get("display_name") or rule_id),
                field=field,
                before=float(old[field]),
                after=float(new[field]),
            ))
    return CalibrationRunOut(
        id=run.id, metric_key=run.metric_key, sample_count=run.sample_count,
        status=run.status, rationale=run.rationale, changes=changes,
        created_at=run.created_at, rolled_back_at=run.rolled_back_at,
    )


def _metric(
    *,
    key: str,
    label: str,
    sample_count: int,
    success_count: int,
    fail_count: int,
    evidence: list[str],
    average_value: float = 0,
    success_word: str = "通过",
) -> CalibrationMetricOut:
    success_rate = round(success_count / sample_count * 100, 1) if sample_count else 0
    if sample_count < 3:
        verdict = "样本不足"
    elif success_rate >= 70:
        verdict = f"{success_word}稳定"
    elif success_rate >= 50:
        verdict = "需要观察"
    else:
        verdict = "需要校准"
    return CalibrationMetricOut(
        key=key,
        label=label,
        sample_count=sample_count,
        success_count=success_count,
        fail_count=fail_count,
        success_rate=success_rate,
        average_value=average_value,
        verdict=verdict,
        evidence=evidence,
    )


def _create_pending_trade_review(trade: TradeLog, db: Session) -> TradeReview:
    db.query(TradeReview).filter(TradeReview.trade_id == trade.id).delete()
    review = TradeReview(
        trade_id=trade.id,
        code=trade.code,
        name=trade.name,
        verdict="深度复盘生成中",
        status="pending",
        discipline_score=0,
        summary="已保存交易记录，正在异步结合大盘、板块、个股分时/行情和交易理由生成深度复盘。",
        stock_context="生成中",
        sector_context="生成中",
        market_context="生成中",
        mistakes=json.dumps(["深度复盘尚未完成。"], ensure_ascii=False),
        avoid_actions=json.dumps(["稍后刷新交易日志查看完整复盘。"], ensure_ascii=False),
        weakness_tags=json.dumps([tag for tag in (trade.human_tags or "").split(",") if tag], ensure_ascii=False),
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return review

def _complete_trade_review_task(trade_id: int) -> None:
    db = SessionLocal()
    try:
        trade = db.get(TradeLog, trade_id)
        if trade is None:
            return
        generated = _generate_trade_review(trade, db)
        review = (
            db.query(TradeReview)
            .filter(TradeReview.trade_id == trade.id)
            .order_by(TradeReview.created_at.desc())
            .first()
        )
        if review is None:
            db.add(generated)
        else:
            _copy_review_fields(review, generated)
        db.commit()
    except Exception as exc:
        db.rollback()
        review = (
            db.query(TradeReview)
            .filter(TradeReview.trade_id == trade_id)
            .order_by(TradeReview.created_at.desc())
            .first()
        )
        if review is not None:
            review.status = "failed"
            review.verdict = "数据缺口"
            review.error_message = str(exc)
            review.summary = "深度复盘生成失败，已保留交易记录；请稍后刷新行情缓存后重新编辑保存触发复盘。"
            db.commit()
    finally:
        db.close()

def _copy_review_fields(target: TradeReview, source: TradeReview) -> None:
    for field in (
        "code",
        "name",
        "verdict",
        "status",
        "discipline_score",
        "summary",
        "stock_context",
        "sector_context",
        "market_context",
        "error_message",
        "mistakes",
        "avoid_actions",
        "weakness_tags",
    ):
        setattr(target, field, getattr(source, field))

def _generate_trade_review(trade: TradeLog, db: Session) -> TradeReview:
    market_context, sector_context, stock_context = _trade_market_context(trade)
    side = trade.side
    reason = trade.reason or ""
    human_tags = [tag for tag in (trade.human_tags or "").split(",") if tag]
    reason_text = f"{reason} {trade.name} {trade.code}"
    mistakes: list[str] = []
    avoid_actions: list[str] = []
    weakness_tags: list[str] = list(dict.fromkeys(human_tags))

    if not trade.compliant:
        mistakes.append("主动标记为违反体系，说明这笔交易已经存在纪律偏离。")
        avoid_actions.append("下一笔交易先写清体系依据，不符合主线/前排/风控时不下单。")
    if not reason.strip():
        mistakes.append("交易理由为空，属于无计划交易。")
        avoid_actions.append("下单前必须写明主线、买点、止损和退出条件。")
        weakness_tags.append("冲动")
    if side in {"买入", "加仓", "做T"}:
        if not _contains_positive_any(reason_text, ("主线", "热点", "板块", "题材", "龙头", "前排", "共振")):
            mistakes.append("买入理由没有说明主线/热点/前排地位。")
            avoid_actions.append("买入前必须回答：它为什么是当前主线里的龙一、龙二或明确前排。")
        if not _contains_positive_any(reason_text, ("止损", "风险", "撤单", "减仓", "退出", "确认位", "失效")):
            mistakes.append("买入理由缺少失效条件或止损/退出计划。")
            avoid_actions.append("每笔买入同时写明失效点、4%止损参考和弱于预期动作。")
        if trade.position_ratio > 0.4 and "集中" not in trade.mode:
            mistakes.append("标准短线模式下单票仓位超过40%上限。")
            avoid_actions.append("标准短线单票不超过40%，非龙一龙二要自动降档。")
            weakness_tags.append("贪婪")
        elif trade.position_ratio > 0.3 and not _contains_any(reason_text, ("龙一", "龙二", "前排", "核心")):
            mistakes.append("仓位偏高，但理由没有证明龙头/前排地位。")
            avoid_actions.append("没有前排证明时，首仓控制在观察仓或补涨仓级别。")
            weakness_tags.append("冲动追高")
    if side in {"卖出", "减仓"}:
        if not _contains_any(reason_text, ("止盈", "止损", "弱于预期", "破位", "退潮", "计划", "纪律")):
            mistakes.append("卖出理由没有绑定计划，容易变成情绪化卖出。")
            avoid_actions.append("卖出前区分：止盈、止损、弱于预期、板块退潮，不能只凭感觉。")
            weakness_tags.append("恐惧")

    if "未在订单流方向估算/题材雷达/涨停天梯中找到明确支持" in sector_context and side in {"买入", "加仓", "做T"}:
        mistakes.append("当前系统证据未确认板块共振，买入证据不足。")
        avoid_actions.append("没有板块订单流方向估算或涨停天梯支撑时，默认降低仓位或只观察。")
    if not mistakes:
        mistakes.append("未发现明显纪律错误，但仍需盘后核对实际走势是否符合预期。")
        avoid_actions.append("保留本次计划模板，盘后复盘是否按计划执行。")

    weakness_tags = _dedupe([tag for tag in weakness_tags if tag] or _infer_weakness_tags(mistakes))
    score = _discipline_score(trade, mistakes)
    verdict = "体系内" if score >= 80 else "存疑" if score >= 60 else "明显偏离"
    summary = _trade_review_summary(trade, verdict, mistakes, avoid_actions)
    return TradeReview(
        trade_id=trade.id,
        code=trade.code,
        name=trade.name,
        verdict=verdict,
        status="done",
        discipline_score=score,
        summary=summary,
        stock_context=stock_context,
        sector_context=sector_context,
        market_context=market_context,
        error_message="",
        mistakes=json.dumps(mistakes, ensure_ascii=False),
        avoid_actions=json.dumps(_dedupe(avoid_actions), ensure_ascii=False),
        weakness_tags=json.dumps(weakness_tags, ensure_ascii=False),
    )

def _trade_market_context(trade: TradeLog) -> tuple[str, str, str]:
    market_context = "行情数据暂不可用，先按交易理由和纪律规则复盘。"
    sector_context = "未在订单流方向估算/题材雷达/涨停天梯中找到明确支持。"
    stock_context = f"{trade.name} {trade.code}：价格{trade.price:.2f}，金额{trade.amount:.2f}，仓位{trade.position_ratio * 100:.1f}%。"
    radar = _get_response_cache("theme-radar")
    if radar is not None:
        strongest = radar.strongest_theme.name if radar.strongest_theme else "暂无"
        market_context = f"市场温度：{radar.market_temperature}；最强题材：{strongest}。"
        for theme in radar.themes[:12]:
            text = f"{theme.name}{''.join(theme.leader_names)}{''.join(theme.related_boards)}"
            if trade.name in text or trade.code in text or _contains_any(trade.reason, tuple(theme.related_boards + [theme.name])):
                sector_context = (
                    f"题材匹配：{theme.name}，阶段：{theme.stage}，评分{theme.score}；"
                    f"核心股：{'、'.join(theme.leader_names[:4]) or '待确认'}。"
                )
                break
    else:
        market_context = "题材雷达暂无缓存：本次保存不阻塞外部行情，盘后刷新题材雷达后可重新评估。"
    ladder = _get_response_cache(f"limit-up-ladder|{_last_trading_day()}")
    if ladder is not None:
        for group in ladder.groups:
            for stock in group.stocks:
                if stock.code == trade.code or stock.name == trade.name:
                    stock_context = (
                        f"{trade.name}位于涨停天梯{group.label}；行业/概念："
                        f"{stock.industry or '待确认'} {'、'.join(stock.concepts[:3])}；"
                        f"封单{stock.sealed_amount:.2f}亿，炸板{stock.break_count}次。"
                    )
                    if sector_context.startswith("未在"):
                        sector_context = f"涨停天梯支持：{stock.industry or '待确认'}，{stock.expectation}"
                    break
    elif sector_context.startswith("未在"):
        sector_context = "涨停天梯暂无缓存：本次保存不阻塞外部行情，盘后刷新涨停天梯后可补充验证。"
    return market_context, sector_context, stock_context

def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword and keyword in text for keyword in keywords)

def _contains_positive_any(text: str, keywords: tuple[str, ...]) -> bool:
    negative_markers = ("没有", "没写", "未写", "缺少", "不明确", "无")
    for keyword in keywords:
        if not keyword:
            continue
        start = text.find(keyword)
        while start >= 0:
            prefix = text[max(0, start - 8):start]
            if not any(marker in prefix for marker in negative_markers):
                return True
            start = text.find(keyword, start + len(keyword))
    return False

def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))

def _infer_weakness_tags(mistakes: list[str]) -> list[str]:
    joined = "".join(mistakes)
    tags: list[str] = []
    if "仓位" in joined or "超过" in joined:
        tags.append("贪婪")
    if "主线" in joined or "前排" in joined:
        tags.append("冲动追高")
    if "理由为空" in joined or "无计划" in joined:
        tags.append("冲动")
    if "卖出" in joined or "感觉" in joined:
        tags.append("恐惧")
    return tags or ["纪律待强化"]

def _discipline_score(trade: TradeLog, mistakes: list[str]) -> int:
    score = 100
    if not trade.compliant:
        score -= 20
    score -= min(45, max(0, len(mistakes) - 1) * 12)
    if trade.position_ratio > 0.4 and "集中" not in trade.mode:
        score -= 15
    if not trade.reason.strip():
        score -= 15
    return max(0, min(100, score))

def _trade_review_summary(trade: TradeLog, verdict: str, mistakes: list[str], avoid_actions: list[str]) -> str:
    mistake = mistakes[0] if mistakes else "未发现明显问题"
    action = avoid_actions[0] if avoid_actions else "继续按计划复盘执行。"
    return f"{trade.side}{trade.name}复盘：{verdict}。核心问题：{mistake} 后续动作：{action}"
