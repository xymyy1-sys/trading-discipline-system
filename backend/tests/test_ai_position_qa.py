from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.models.trading import AiAnalysisCache, ExpectationRevision, Holding
from app.services import ai_position_qa as service
from app.services.ai_position_qa import PositionQaResult


class DumpNamespace(SimpleNamespace):
    def model_dump(self, **_kwargs):
        return dict(self.__dict__)


def _install_context_dependencies(monkeypatch, db_session) -> Holding:
    holding = Holding(
        code="600584",
        name="长电科技",
        quantity=200,
        cost_price=94.75,
        current_price=101.11,
        total_asset=100_000,
        position_type="趋势仓",
        next_discipline="冲高不承接则分批减仓",
    )
    db_session.add(holding)
    db_session.flush()
    db_session.add(ExpectationRevision(
        expectation_snapshot_id=1,
        version=1,
        trade_date="2026-07-14",
        code="600584",
        name="长电科技",
        stage="第一阶段确认",
        trigger="collector",
        base_expectation="REPAIR",
        expected_open_low=-2,
        expected_open_high=2.5,
        actual_open_pct=-0.88,
        actual_change_pct=1.2,
        expectation_gap_score=6,
        expectation_result="MATCHED",
        state_transition="RECOVERY_PENDING",
        confidence=0.76,
        volume_price_state="收复VWAP",
        vwap=100.2,
        price_vs_vwap=0.9,
        data_quality="realtime",
        evidence_json='["水下反弹后收复VWAP"]',
        counter_evidence_json='["板块资金尚未回到前高"]',
        invalid_conditions_json='["再次跌破VWAP"]',
        suggestion="等待回踩确认",
    ))
    db_session.commit()

    now = datetime(2026, 7, 14, 10, 35)
    expectation = DumpNamespace(
        created_at=now,
        trade_date="2026-07-14",
        stage="第一阶段确认",
        base_expectation="REPAIR",
        expected_open_low=-2,
        expected_open_high=2.5,
        actual_open_pct=-0.88,
        actual_change_pct=1.2,
        expectation_gap_score=6,
        expectation_result="MATCHED",
        state_transition="RECOVERY_PENDING",
        confidence=0.76,
        evidence=["水下反弹后收复VWAP"],
        counter_evidence=["板块资金尚未回到前高"],
    )
    volume = DumpNamespace(
        captured_at=now,
        data_source="东方财富分钟行情",
        vwap_reliable=True,
        price=101.11,
        vwap=100.2,
        high_price=103.0,
        low_price=96.8,
        volume_ratio=1.3,
        pattern="V形反转待回踩确认",
    )
    execution = DumpNamespace(
        updated_at=now,
        state="NORMAL_HOLD",
        sellable_quantity=200,
        recommended_action="等待回踩确认",
        allowed_actions=["不追卖"],
        forbidden_actions=["禁止无确认补仓"],
        invalid_conditions=["再次跌破VWAP且放量"],
        recovery_conditions=["回踩VWAP不破"],
    )
    timeline = [DumpNamespace(
        captured_at=now,
        event_type="VWAP_RECOVERED",
        severity="info",
        evidence=["10:35收复分时均价"],
    )]
    entry_discipline = DumpNamespace(
        decision="WAIT_RETEST",
        label="等待回踩确认，当前不下单",
        risk_level="MEDIUM",
        allowed_position_ratio=0,
        evidence=["当前仍在冲高后的确认窗口"],
        recheck_conditions=["回踩分时均价不破后重新抬高"],
    )
    card = DumpNamespace(
        code=holding.code,
        name=holding.name,
        current_price=101.11,
        change_pct=1.2,
        industry="半导体",
        concepts=["芯片"],
        data_quality="realtime",
        expectation=expectation,
        volume_price=volume,
        execution_state=execution,
        entry_discipline=entry_discipline,
        timeline=timeline,
        minute_chart=[
            {"time": "09:31", "price": 98.0, "vwap": 98.0, "amount": 0.2},
            {"time": "10:35", "price": 101.11, "vwap": 100.2, "amount": 0.3},
        ],
    )
    sector = DumpNamespace(name="半导体", model_dump=lambda **_kwargs: {"name": "半导体"})
    regime = DumpNamespace(
        captured_at=now,
        source="东方财富全A",
        trade_date="2026-07-14",
        regime_code="SHRINK_ROTATION",
        regime_name="缩量轮动",
        risk_level="中高",
        opportunity_score=35,
        loss_score=65,
        liquidity_score=40,
        up_count=1800,
        down_count=3500,
        limit_up_count=35,
        limit_down_count=18,
        market_main_net_inflow_yi=-320,
        volume_ratio_5d=0.72,
        positive_sector_ratio=0.25,
        strongest_sectors=[sector],
        weakest_sectors=[],
        allowed_actions=["只做确认后的前排"],
        forbidden_actions=["禁止无确认抄底"],
        evidence=["缩量且下跌家数占优"],
        data_quality="ok",
        missing_fields=[],
    )
    monkeypatch.setattr(service, "decision_card", lambda *_args, **_kwargs: card)
    monkeypatch.setattr(service, "get_market_regime", lambda *_args, **_kwargs: regime)
    monkeypatch.setattr(service, "build_market_reflexivity", lambda *_args: {"current_scenario": "REBOUND_ABSORPTION"})
    monkeypatch.setattr(service, "build_stock_reflexivity", lambda *_args: {"as_of": str(now), "current_scenario": "REBOUND_ABSORPTION"})
    monkeypatch.setattr(service, "_holding_theme_profile", lambda *_args: {"industry": "半导体", "primary": "半导体", "concepts": ["芯片"]})
    monkeypatch.setattr(service, "_cached_holding_theme_flow_profile", lambda *_args: {
        "sectors": ["半导体"], "concept_sectors": ["芯片"], "rank": 4,
        "current": 12.5, "main": 8.2, "peak": 20.0, "pullback": 7.5,
        "pullback_pct": 37.5, "acceleration": -2.1,
        "as_of": "2026-07-14T10:34:00+08:00", "source": "东方财富行业资金",
        "data_quality": "cached_source_timestamped",
    })
    monkeypatch.setattr(service._global_market_service, "snapshot", lambda **_kwargs: {
        "as_of": "2026-07-14T08:00:00+08:00", "data_quality": "ok", "sources": ["Yahoo"],
        "korea_indices": [], "korea_equities": [], "us_indices": [], "us_sector_rank": [], "notes": [],
    })
    monkeypatch.setattr(service, "_related_news", lambda *_args: ([{
        "published_at": "2026-07-14T09:50:00+08:00", "title": "半导体板块异动",
        "source": "东方财富快讯", "url": "https://example.test/news",
    }], []))
    return holding


def test_position_context_contains_traceable_decision_evidence(db_session, monkeypatch):
    _install_context_dependencies(monkeypatch, db_session)

    context = service.build_position_context(db_session, "600584")

    assert context["market_regime"]["evidence_id"] == "MKT-1"
    assert context["holding_facts"]["data"]["name"] == "长电科技"
    assert context["holding_facts"]["data"]["profit_pct"] is not None
    assert context["expectation_version_chain"]["data"]["versions"][0]["version"] == 1
    assert context["minute_volume_price"]["data"]["vwap_reliable"] is True
    assert context["execution_state"]["data"]["sellable_quantity"] == 200
    assert context["entry_discipline"]["data"]["decision"] == "WAIT_RETEST"
    assert context["reflexivity"]["data"]["stock"]["current_scenario"] == "REBOUND_ABSORPTION"
    assert context["related_news"]["data"][0]["url"] == "https://example.test/news"
    assert context["missing_fields"] == []


def test_position_answer_reuses_same_question_and_context_cache(db_session, monkeypatch):
    context_calls = {"value": 0}

    def changing_clock_context(*_args):
        context_calls["value"] += 1
        return {
            "context_as_of": f"2026-07-14T10:3{context_calls['value']}:00+08:00",
            "missing_fields": [],
            "market_regime": {
                "evidence_id": "MKT-1",
                "as_of": "2026-07-14T10:30:00+08:00",
                "data": {"risk_level": "中高"},
            },
            "holding_facts": {"evidence_id": "HLD-1", "as_of": "2026-07-14T10:30:00+08:00", "data": {"quantity": 200}},
        }

    monkeypatch.setattr(service, "build_position_context", changing_clock_context)
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(
        ai_api_key="test-key", ai_base_url="https://api.deepseek.test", ai_model="deepseek-reasoner",
    ))
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "事实：等待量价确认[MKT-1][HLD-1]"}}]}

    def fake_post(*_args, **kwargs):
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr(service.httpx, "post", fake_post)

    first = service.generate_position_answer(db_session, "600584", "该不该卖？")
    second = service.generate_position_answer(db_session, "600584", "该不该卖？")

    assert first.cached is False
    assert second.cached is True
    assert second.row.id == first.row.id
    assert len(calls) == 1
    assert context_calls["value"] == 2
    assert second.context_as_of == "2026-07-14T10:32:00+08:00"
    assert "结构化证据包" in calls[0]["json"]["messages"][1]["content"]
    assert calls[0]["headers"]["Authorization"] == "Bearer test-key"


def test_position_answer_invalidates_cache_when_evidence_value_changes(db_session, monkeypatch):
    risk = {"value": "中高"}

    def context(*_args):
        return {
            "context_as_of": "2026-07-14T10:35:00+08:00",
            "missing_fields": [],
            "market_regime": {"evidence_id": "MKT-1", "as_of": "2026-07-14T10:35:00+08:00", "data": {"risk_level": risk["value"]}},
            "holding_facts": {"evidence_id": "HLD-1", "as_of": "2026-07-14T10:35:00+08:00", "data": {"quantity": 200}},
        }

    monkeypatch.setattr(service, "build_position_context", context)
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(
        ai_api_key="test-key", ai_base_url="https://api.deepseek.test", ai_model="deepseek-reasoner",
    ))
    calls = {"value": 0}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            calls["value"] += 1
            return {"choices": [{"message": {"content": f"风险证据版本{calls['value']}[MKT-1][HLD-1]"}}]}

    monkeypatch.setattr(service.httpx, "post", lambda *_args, **_kwargs: Response())

    service.generate_position_answer(db_session, "600584", "该不该卖？")
    risk["value"] = "高"
    second = service.generate_position_answer(db_session, "600584", "该不该卖？")

    assert second.cached is False
    assert calls["value"] == 2
    assert second.row.content == "风险证据版本2[MKT-1][HLD-1]"


def test_position_answer_force_regenerates(db_session, monkeypatch):
    context = {
        "context_as_of": "2026-07-14T10:35:00+08:00",
        "missing_fields": ["可靠的真实分钟VWAP"],
        "market_regime": {"evidence_id": "MKT-1", "as_of": "2026-07-14T10:35:00+08:00", "data": {}},
        "holding_facts": {"evidence_id": "HLD-1", "as_of": "2026-07-14T10:35:00+08:00", "data": {}},
    }
    monkeypatch.setattr(service, "build_position_context", lambda *_args: context)
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(
        ai_api_key="test-key", ai_base_url="https://api.deepseek.test", ai_model="deepseek-reasoner",
    ))
    counter = {"value": 0}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            counter["value"] += 1
            return {"choices": [{"message": {"content": f"第{counter['value']}次审查[MKT-1][HLD-1]"}}]}

    monkeypatch.setattr(service.httpx, "post", lambda *_args, **_kwargs: Response())

    service.generate_position_answer(db_session, "600584", "能否加仓？")
    result = service.generate_position_answer(db_session, "600584", "能否加仓？", force=True)

    assert result.cached is False
    assert result.row.content == "第2次审查[MKT-1][HLD-1]"
    assert result.missing_fields == ["可靠的真实分钟VWAP"]


def test_position_qa_requires_real_holding(db_session):
    try:
        service.build_position_context(db_session, "000001")
    except ValueError as exc:
        assert "不在当前持仓" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_position_qa_rejects_zero_quantity_holding(db_session, monkeypatch):
    holding = Holding(code="000001", name="已清仓", quantity=0, cost_price=10, current_price=10, total_asset=100_000)
    db_session.add(holding)
    db_session.commit()

    try:
        service.build_position_context(db_session, "000001")
    except ValueError as exc:
        assert "不在当前持仓" in str(exc)
    else:
        raise AssertionError("expected zero-quantity holding to be rejected")


def test_position_qa_api_returns_context_metadata(client, db_session, monkeypatch):
    from app.api.routes import ai as ai_route

    row = AiAnalysisCache(
        scope="position_qa",
        target="600584:questionhash",
        model="deepseek-reasoner",
        input_hash="inputhash",
        content="直接回答：等待回踩确认[VP-1]",
        status="completed",
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    monkeypatch.setattr(ai_route, "generate_position_answer", lambda *_args, **_kwargs: PositionQaResult(
        row=row,
        question="该不该卖？",
        cached=True,
        context_as_of="2026-07-14T10:35:00+08:00",
        missing_fields=["可靠的真实分钟VWAP"],
    ))

    response = client.post("/api/ai/position-qa/600584", json={"question": "该不该卖？", "force": False})

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == "600584"
    assert payload["cached"] is True
    assert payload["missing_fields"] == ["可靠的真实分钟VWAP"]
