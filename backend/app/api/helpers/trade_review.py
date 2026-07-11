import json
from datetime import datetime
from typing import Any
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models.trading import TradeLog, TradeReview
from app.schemas.trading import TradeLogOut, TradeReviewOut
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

    if "未在资金流/题材雷达/涨停天梯中找到明确支持" in sector_context and side in {"买入", "加仓", "做T"}:
        mistakes.append("当前系统证据未确认板块共振，买入证据不足。")
        avoid_actions.append("没有板块资金或涨停天梯支撑时，默认降低仓位或只观察。")
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
    sector_context = "未在资金流/题材雷达/涨停天梯中找到明确支持。"
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
