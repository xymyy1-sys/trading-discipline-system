import json
from datetime import datetime

from app.models.trading import (
    DataCaptureSnapshot,
    SimulationAccount,
    SimulationClosedTrade,
    SimulationFill,
    SimulationOrder,
    SimulationShadowDecision,
    SystemImprovementProposal,
)
from app.services.system_evolution import generate_system_improvement_proposals, system_evolution_report


def add_learning_sample(db_session, *, account_id: int, suffix: str, diagnostic: str):
    db_session.add(DataCaptureSnapshot(
        trade_date="2026-08-07",
        captured_at=datetime(2026, 8, 7, 15, 5),
        source="simulation-learning",
        data_type="ai_trade_learning",
        target_code=f"fill:{suffix}",
        target_name="测试成交",
        raw_value_json="{}",
        normalized_value_json=json.dumps({
            "account_id": account_id,
            "status": "evaluated",
            "diagnostic_tags": [diagnostic],
        }, ensure_ascii=False),
        quality="complete",
        is_complete=True,
        status="evaluated",
    ))


def test_repeated_trade_facts_create_auditable_development_proposal(db_session):
    account = SimulationAccount(
        name="系统进化测试",
        initial_cash=20_000,
        cash=20_000,
        account_type="shadow",
        automation_key="system-evolution-test",
    )
    other = SimulationAccount(
        name="其他账户",
        initial_cash=20_000,
        cash=20_000,
        account_type="shadow",
        automation_key="system-evolution-other",
    )
    db_session.add_all([account, other])
    db_session.flush()
    add_learning_sample(db_session, account_id=account.id, suffix="1", diagnostic="买入偏离VWAP过远")
    add_learning_sample(db_session, account_id=account.id, suffix="2", diagnostic="买入偏离VWAP过远")
    add_learning_sample(db_session, account_id=other.id, suffix="3", diagnostic="卖出后出现明显修复")
    db_session.commit()

    rows = generate_system_improvement_proposals(db_session, account, trade_date="2026-08-07")
    assert len(rows) == 1
    assert rows[0].module_key == "预期×量价"
    assert rows[0].sample_count == 2
    assert "打板策略使用独立晋级" in rows[0].proposed_change
    assert "影子" in rows[0].acceptance_json

    # Re-running the close job is idempotent and must not duplicate proposals.
    generate_system_improvement_proposals(db_session, account, trade_date="2026-08-07")
    assert db_session.query(SystemImprovementProposal).count() == 1
    report = system_evolution_report(db_session, account)
    assert report["proposals"][0]["proposal_key"].startswith("EVO-")
    assert report["governance"]["automatic_code_change"] is False


def test_losing_reference_module_creates_function_level_proposal(db_session):
    account = SimulationAccount(
        name="模块归因测试",
        initial_cash=20_000,
        cash=20_000,
        account_type="shadow",
        automation_key="module-attribution-test",
    )
    db_session.add(account)
    db_session.flush()
    now = datetime(2026, 8, 7, 10, 0)
    for index in range(3):
        order = SimulationOrder(
            account_id=account.id,
            decision_evidence_snapshot_id=100 + index,
            strategy_source="expectation_volume_price",
            code=f"60000{index}",
            name=f"样本{index}",
            side="BUY",
            order_type="LIMIT",
            limit_price=10,
            quantity=100,
            status="FILLED",
            trade_date="2026-08-07",
            submitted_at=now,
            last_evaluated_at=now,
        )
        db_session.add(order)
        db_session.flush()
        db_session.add(SimulationShadowDecision(
            account_id=account.id,
            signal_key=f"signal-{index}",
            strategy_source="expectation_volume_price",
            source_kind="autonomous_universe_selection",
            trade_date="2026-08-07",
            evaluated_at=now,
            code=order.code,
            name=order.name,
            intent="BUY",
            side="BUY",
            quantity=100,
            status="ORDER_CREATED",
            order_id=order.id,
            evidence_json='["来源标签=抓涨停"]',
        ))
        fill = SimulationFill(
            order_id=order.id,
            account_id=account.id,
            fill_evidence_snapshot_id=200 + index,
            strategy_source="expectation_volume_price",
            code=order.code,
            name=order.name,
            side="BUY",
            price=10,
            quantity=100,
            gross_amount=1000,
            net_cash_flow=-1000,
            trade_date="2026-08-07",
            filled_at=now,
        )
        db_session.add(fill)
        db_session.flush()
        db_session.add(SimulationClosedTrade(
            account_id=account.id,
            lot_id=300 + index,
            code=order.code,
            name=order.name,
            strategy_source="expectation_volume_price",
            entry_order_id=order.id,
            entry_fill_id=fill.id,
            closing_order_id=400 + index,
            closing_fill_id=500 + index,
            quantity=100,
            entry_average_price=10,
            exit_average_price=9.8,
            entry_gross_amount=1000,
            exit_gross_amount=980,
            realized_pnl=-20,
            return_pct=-2,
            opened_at=now,
            closed_at=now,
        ))
    db_session.commit()

    rows = generate_system_improvement_proposals(db_session, account, trade_date="2026-08-07")
    module_rows = [row for row in rows if row.level == "module" and row.module_key == "抓涨停"]
    assert len(module_rows) == 1
    assert module_rows[0].sample_count == 3
    assert "漏选、误选" in module_rows[0].proposed_change
