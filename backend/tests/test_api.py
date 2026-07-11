def test_health_check(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"

def test_market_sector_flow(client):
    response = client.get("/api/market/sector-flow")
    assert response.status_code == 200
    data = response.json()
    assert "source" in data
    assert "inflow" in data
    assert "outflow" in data
    assert isinstance(data["inflow"], list)
    assert isinstance(data["outflow"], list)


def test_review_calibration_summary(client, db_session):
    from app.models.trading import NextDayPlan, TradeLog, TradeReview

    trade = TradeLog(
        code="600010",
        name="校准样本",
        side="买入",
        price=10,
        quantity=1000,
        amount=10000,
        total_asset=100000,
        position_ratio=0.1,
        cost_price=10,
        stop_loss_price=9.6,
        reason="无计划追高",
        mode="标准短线模式",
        compliant=False,
        human_tags="冲动",
    )
    db_session.add(trade)
    db_session.flush()
    db_session.add(TradeReview(
        trade_id=trade.id,
        code=trade.code,
        name=trade.name,
        verdict="明显偏离",
        status="done",
        discipline_score=45,
        summary="买入校准样本复盘：明显偏离。",
        stock_context="个股证据不足",
        sector_context="板块证据不足",
        market_context="市场弱",
        mistakes='["无计划交易"]',
        avoid_actions='["下次先写计划"]',
        weakness_tags='["冲动"]',
    ))
    db_session.add(NextDayPlan(
        plan_date="2026-07-13",
        plan_type="holding",
        code="600011",
        name="未复盘计划",
    ))
    db_session.add(NextDayPlan(
        plan_date="2026-07-13",
        plan_type="holding",
        code="600012",
        name="已复盘计划",
        review_expectation="弱于预期",
        review_execution="未执行减仓",
        review_deviation="幻想回拉",
    ))
    db_session.commit()

    response = client.get("/api/review-calibration/summary")

    assert response.status_code == 200
    data = response.json()
    assert data["avg_discipline_score"] == 45
    assert data["missing_plan_review_count"] == 1
    assert data["plan_review_count"] == 1
    assert any(item["title"] == "纪律评分低于 60" for item in data["issues"])
    assert data["recent_plan_deviations"][0]["severity"] == "高"
