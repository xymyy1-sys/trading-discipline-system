from __future__ import annotations

import logging
import re
from typing import Iterable

import dingtalk_stream

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.trading import Holding
from app.services.ai_analysis import generate_analysis
from app.services.ai_position_qa import generate_position_answer


LOGGER = logging.getLogger("dingtalk_stream_bot")
CODE_PATTERN = re.compile(r"(?<!\d)([036]\d{5})(?!\d)")
HELP_TEXT = """### 知行交易驾驶舱 · 群内问答

- `持仓`：查看当前持仓标的（隐藏金额和盈亏）。
- `600584 现在该卖吗`：调用该持仓的完整数据证据包回答。
- `长电科技该减仓吗`：也可以直接使用当前持仓名称。
- `今日决策`：生成全市场与持仓证据审查摘要。
- `状态`：检查机器人连接与权限状态。

回答仅用于证据审查，不会自动操作真实账户。"""


def _allowed_ids(raw: str) -> set[str]:
    return {item.strip() for item in re.split(r"[,;\s]+", raw or "") if item.strip()}


def is_sender_authorized(message: dingtalk_stream.ChatbotMessage, allowed: Iterable[str], allow_admin: bool) -> bool:
    identities = {str(message.sender_staff_id or ""), str(message.sender_id or "")}
    configured = set(allowed)
    return bool(identities & configured) or (allow_admin and bool(message.is_admin))


def clean_question(text: str) -> str:
    return re.sub(r"@\S+", "", text or "").strip()


def resolve_holding(question: str, holdings: list[Holding]) -> Holding | None:
    code_match = CODE_PATTERN.search(question)
    if code_match:
        code = code_match.group(1)
        return next((item for item in holdings if str(item.code).zfill(6) == code), None)
    named = [item for item in holdings if item.name and item.name in question]
    return named[0] if len(named) == 1 else None


def holding_summary(holdings: list[Holding]) -> str:
    if not holdings:
        return "### 当前持仓\n\n系统当前没有持仓记录。"
    lines = ["### 当前持仓（隐私模式）", ""]
    for item in holdings:
        lines.append(f"- **{item.name}** `{str(item.code).zfill(6)}` · {item.position_type or '待判定'}")
    lines.extend(["", "金额、数量、成本和盈亏未在群消息中展示。发送“代码 + 问题”可进行证据审查。"]) 
    return "\n".join(lines)


class TradingChatbotHandler(dingtalk_stream.AsyncChatbotHandler):
    def __init__(self) -> None:
        super().__init__(max_workers=4)
        settings = get_settings()
        self.allowed = _allowed_ids(settings.dingtalk_stream_allowed_users)
        self.allow_admin = settings.dingtalk_stream_allow_admin

    def _reply(self, incoming: dingtalk_stream.ChatbotMessage, title: str, content: str) -> None:
        response = self.reply_markdown(title, content[:12000], incoming)
        if response is None:
            LOGGER.error("DingTalk reply failed for message_id=%s", incoming.message_id)

    def process(self, callback_message: dingtalk_stream.CallbackMessage) -> None:
        incoming = dingtalk_stream.ChatbotMessage.from_dict(callback_message.data)
        if incoming.message_type not in {"text", "richText"}:
            self._reply(incoming, "暂不支持该消息类型", "目前只处理文字问题，请发送“帮助”查看用法。")
            return
        question = clean_question(" ".join(incoming.get_text_list() or []))
        if not is_sender_authorized(incoming, self.allowed, self.allow_admin):
            identity = incoming.sender_staff_id or incoming.sender_id or "未知"
            self._reply(
                incoming,
                "访问被拒绝",
                f"该机器人包含持仓相关信息，仅允许授权用户或群管理员访问。\n\n你的用户标识：`{identity}`",
            )
            return
        if not question or question in {"帮助", "/help", "help"}:
            self._reply(incoming, "使用帮助", HELP_TEXT)
            return
        if question in {"状态", "/status"}:
            self._reply(incoming, "机器人状态", "### 连接正常\n\nStream 双向通道已连接；当前用户已通过权限校验。")
            return

        db = SessionLocal()
        try:
            holdings = db.query(Holding).filter(Holding.quantity > 0).order_by(Holding.updated_at.desc()).all()
            if question in {"持仓", "当前持仓", "持仓概览"}:
                self._reply(incoming, "当前持仓", holding_summary(holdings))
                return
            holding = resolve_holding(question, holdings)
            if holding:
                result = generate_position_answer(db, holding.code, question, force=False)
                prefix = f"### {holding.name} `{str(holding.code).zfill(6)}`\n\n"
                suffix = f"\n\n---\n数据时点：{result.context_as_of}；回答不会自动下单。"
                self._reply(incoming, f"{holding.name}持仓研判", prefix + result.row.content + suffix)
                return
            if any(token in question for token in ("今日决策", "市场", "大盘", "今日策略")):
                row = generate_analysis(db, "market", "today", force=False)
                self._reply(incoming, "今日决策证据审查", row.content + "\n\n---\n仅依据系统当前数据，不会自动下单。")
                return
            self._reply(incoming, "需要明确标的", "未识别到当前持仓代码或名称。\n\n" + HELP_TEXT)
        except Exception as exc:
            LOGGER.exception("DingTalk question failed")
            self._reply(incoming, "处理失败", f"本次问题处理失败：`{exc.__class__.__name__}`。请稍后重试。")
        finally:
            db.close()


def main() -> None:
    settings = get_settings()
    if not settings.dingtalk_stream_enabled:
        raise RuntimeError("DINGTALK_STREAM_ENABLED is false")
    if not settings.dingtalk_stream_client_id or not settings.dingtalk_stream_client_secret:
        raise RuntimeError("DingTalk Stream credentials are incomplete")
    credential = dingtalk_stream.Credential(
        settings.dingtalk_stream_client_id,
        settings.dingtalk_stream_client_secret,
    )
    client = dingtalk_stream.DingTalkStreamClient(credential, logger=LOGGER)
    client.register_callback_handler(dingtalk_stream.ChatbotMessage.TOPIC, TradingChatbotHandler())
    LOGGER.info("Starting DingTalk Stream bot")
    client.start_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
