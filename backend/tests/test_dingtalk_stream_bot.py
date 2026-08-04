from types import SimpleNamespace

from app.services.dingtalk_stream_bot import clean_question, is_sender_authorized, resolve_holding


def holding(code: str, name: str):
    return SimpleNamespace(code=code, name=name)


def message(*, staff_id: str = "", sender_id: str = "", is_admin: bool = False):
    return SimpleNamespace(sender_staff_id=staff_id, sender_id=sender_id, is_admin=is_admin)


def test_stream_bot_requires_allowlist_or_admin():
    assert is_sender_authorized(message(staff_id="owner"), {"owner"}, False)
    assert is_sender_authorized(message(staff_id="admin", is_admin=True), set(), True)
    assert not is_sender_authorized(message(staff_id="guest"), {"owner"}, False)


def test_clean_question_removes_group_mention():
    assert clean_question("@知行交易驾驶舱 600584 该卖吗") == "600584 该卖吗"


def test_resolve_holding_by_code_or_unique_name():
    holdings = [holding("600584", "长电科技"), holding("000725", "京东方A")]
    assert resolve_holding("600584 该卖吗", holdings).name == "长电科技"
    assert resolve_holding("京东方A是否继续持有", holdings).code == "000725"
    assert resolve_holding("兆易创新怎么样", holdings) is None
