from datetime import datetime

from app.services import market_data
from app.schemas.trading import (
    LimitUpAtmosphereMetrics,
    LimitUpAtmosphereOut,
    LimitUpClusterOut,
    LimitUpGroupOut,
    LimitUpLadderOut,
    LimitUpStockOut,
    ThemeRadarItem,
    ThemeRadarOut,
)
from app.services.market_data import MarketDataProvider


def _stock(code: str, name: str, level: int) -> LimitUpStockOut:
    return LimitUpStockOut(
        code=code,
        name=name,
        price=10,
        change_pct=10,
        amount=8,
        turnover=12,
        sealed_amount=1.2,
        consecutive_limit_days=level,
        industry="半导体",
        concepts=["半导体"],
    )


def _ladder() -> LimitUpLadderOut:
    stocks = [
        _stock("600001", "测试一", 4),
        _stock("600002", "测试二", 2),
        _stock("600003", "测试三", 1),
    ]
    return LimitUpLadderOut(
        source="eastmoney-limit-up-pool",
        trade_date="2026-07-14",
        updated_at=datetime.now(),
        groups=[LimitUpGroupOut(level=4, label="4板", stocks=stocks)],
        clusters=[LimitUpClusterOut(
            name="半导体",
            count=2,
            highest_level=4,
            stocks=["测试一", "测试二"],
            expectation="测试",
        )],
        summary=[],
        notes=[],
    )


def _previous_rows() -> list[dict]:
    return [
        {"代码": "600001", "名称": "测试一", "连板数": 3, "所属行业": "半导体"},
        {"代码": "600002", "名称": "测试二", "连板数": 1, "所属行业": "半导体"},
    ]


def _theme_radar(
    *,
    rank: int = 1,
    score: int = 82,
    stage: str = "发酵",
    net_inflow: float = 18.0,
    main_inflow: float = 9.0,
) -> ThemeRadarOut:
    theme = ThemeRadarItem(
        name="存储芯片",
        board_code="BK1036",
        theme_type="主线题材",
        related_boards=["半导体", "芯片"],
        stage=stage,
        stage_reason=f"测试题材处于{stage}阶段",
        score=score,
        rank=rank,
        change_pct=3.8,
        net_inflow=net_inflow,
        main_inflow=main_inflow,
        limit_up_count=3,
        stock_count=30,
        leader_names=["测试一"],
        core_stocks=[],
        timeline=[],
        resonance_tags=["资金净流入放大", "涨停扩散"],
        action="测试行动",
        risk="测试风险",
    )
    return ThemeRadarOut(
        source="eastmoney-test-theme-radar",
        updated_at=datetime.now(),
        market_temperature="偏强",
        strongest_theme=theme,
        resonance=[theme],
        themes=[theme],
        notes=[],
    )


def _stub_theme_radar(monkeypatch, provider: MarketDataProvider, **kwargs) -> None:
    radar = _theme_radar(**kwargs)
    monkeypatch.setattr(provider, "theme_radar", lambda **_: radar)


def _raw_pool_row(code: str = "600001") -> dict:
    return {
        "代码": code,
        "名称": f"真实{code[-3:]}",
        "最新价": 10.0,
        "涨跌幅": 10.0,
        "成交额": 8_000_000_000,
        "换手率": 12.0,
        "封板资金": 120_000_000,
        "炸板次数": 0,
        "连板数": 1,
        "所属行业": "半导体",
    }


def test_default_ladder_falls_back_when_current_real_shape_is_unavailable(monkeypatch):
    provider = MarketDataProvider()
    calls: list[str] = []
    monkeypatch.setattr(
        market_data,
        "_limit_up_default_candidate_dates",
        lambda: ["2026-07-15", "2026-07-14", "2026-07-13"],
    )

    def fetch_pool(trade_date: str):
        calls.append(trade_date)
        if trade_date == "2026-07-15":
            # This is the same failure that becomes source=unavailable and an
            # empty group list in the production provider response.
            raise ValueError("empty direct limit-up pool")
        return [_raw_pool_row()]

    monkeypatch.setattr(provider, "_fetch_limit_up_pool_raw", fetch_pool)

    result = provider.limit_up_ladder(force_refresh=True)

    assert result.source == "东方财富涨停池"
    assert result.trade_date == "2026-07-14"
    assert result.groups and result.groups[0].stocks[0].code == "600001"
    assert calls == ["2026-07-15", "2026-07-14"]


def test_default_ladder_switches_to_current_only_after_non_empty_pool(monkeypatch):
    provider = MarketDataProvider()
    calls: list[str] = []
    monkeypatch.setattr(
        market_data,
        "_limit_up_default_candidate_dates",
        lambda: ["2026-07-15", "2026-07-14"],
    )

    def fetch_pool(trade_date: str):
        calls.append(trade_date)
        return [_raw_pool_row("600015")]

    monkeypatch.setattr(provider, "_fetch_limit_up_pool_raw", fetch_pool)

    result = provider.limit_up_ladder(force_refresh=True)

    assert result.trade_date == "2026-07-15"
    assert calls == ["2026-07-15"]


def test_default_candidate_dates_keep_pre_market_on_prior_weekday():
    assert market_data._limit_up_default_candidate_dates(
        datetime(2026, 7, 15, 9, 29), lookback=2
    ) == ["2026-07-14", "2026-07-13"]
    assert market_data._limit_up_default_candidate_dates(
        datetime(2026, 7, 15, 9, 30), lookback=2
    ) == ["2026-07-15", "2026-07-14"]
    assert market_data._limit_up_default_candidate_dates(
        datetime(2026, 7, 19, 12, 0), lookback=2
    ) == ["2026-07-17", "2026-07-16"]


def test_atmosphere_default_reuses_ladder_resolved_trade_date(monkeypatch):
    provider = MarketDataProvider()
    _stub_theme_radar(monkeypatch, provider)
    ladder = _ladder()
    ladder.trade_date = "2026-07-09"
    seen: dict[str, str | None] = {}

    def resolved_ladder(*, trade_date=None, force_refresh=False):
        seen["requested"] = trade_date
        return ladder

    monkeypatch.setattr(provider, "limit_up_ladder", resolved_ladder)
    monkeypatch.setattr(
        provider,
        "_fetch_broken_limit_pool_raw",
        lambda date_text: seen.setdefault("broken", date_text) and [],
    )
    monkeypatch.setattr(
        provider,
        "_fetch_dated_pool_total",
        lambda _endpoint, date_text: seen.setdefault("down", date_text) and 0,
    )
    monkeypatch.setattr(
        provider,
        "_find_previous_limit_up_pool",
        lambda *_: (_ for _ in ()).throw(ValueError("no previous sample")),
    )

    result = provider.limit_up_atmosphere(force_refresh=True)

    assert seen["requested"] is None
    assert seen["broken"] == "20260709"
    assert seen["down"] == "20260709"
    assert result.trade_date == "2026-07-09"


def test_limit_up_atmosphere_uses_real_pool_and_premium_samples(monkeypatch):
    provider = MarketDataProvider()
    _stub_theme_radar(monkeypatch, provider)
    monkeypatch.setattr(provider, "limit_up_ladder", lambda **_: _ladder())
    monkeypatch.setattr(provider, "_fetch_broken_limit_pool_raw", lambda *_: [])
    monkeypatch.setattr(provider, "_fetch_dated_pool_total", lambda *_: 0)
    monkeypatch.setattr(
        provider,
        "_find_previous_limit_up_pool",
        lambda *_: ("2026-07-13", _previous_rows()),
    )
    monkeypatch.setattr(
        provider,
        "_fetch_current_stock_quotes",
        lambda *_: ({
            "600001": {
                "trade_date": "20260714",
                "open": 11.2,
                "prev_close": 11,
                "change_pct": 4.0,
            },
            "600002": {
                "trade_date": "20260714",
                "open": 10.3,
                "prev_close": 10,
                "change_pct": 2.0,
            },
        }, "eastmoney-test-quotes"),
    )

    result = provider.limit_up_atmosphere(
        trade_date="2026-07-14", force_refresh=True
    )

    assert result.metrics.limit_up_count == 3
    assert result.metrics.broken_count == 0
    assert result.metrics.seal_rate == 100
    assert result.metrics.promotion_rate == 100
    assert result.metrics.next_day_average_open_pct == 2.41
    assert result.metrics.next_day_low_open_ratio == 0
    assert result.metrics.next_day_average_premium_pct == 3
    assert result.decision == "ALLOW"
    assert any("低开比例" in item for item in result.evidence)
    assert result.theme_ladders
    theme = result.theme_ladders[0]
    assert theme.name == "半导体"
    assert theme.first_board_count == 1
    assert theme.second_board_count == 1
    assert theme.high_board_count == 1
    assert theme.completeness_label == "多层梯队已成形"
    assert theme.action.startswith("允许观察前排")
    assert theme.mainline_level == "核心主线"
    assert theme.stage == "发酵"
    assert theme.max_position_ratio == 0.1
    assert theme.identity_roles[0].max_position_ratio == 0.1
    assert any("龙头候选" in item.roles for item in theme.identity_roles)
    assert "不代表已知主力意图" in result.role_disclaimer


def test_limit_up_atmosphere_never_allows_when_historical_quotes_are_missing(monkeypatch):
    provider = MarketDataProvider()
    _stub_theme_radar(monkeypatch, provider)
    monkeypatch.setattr(provider, "limit_up_ladder", lambda **_: _ladder())
    monkeypatch.setattr(provider, "_fetch_broken_limit_pool_raw", lambda *_: [])
    monkeypatch.setattr(provider, "_fetch_dated_pool_total", lambda *_: 0)
    monkeypatch.setattr(
        provider,
        "_find_previous_limit_up_pool",
        lambda *_: ("2026-07-13", _previous_rows()),
    )
    monkeypatch.setattr(
        provider,
        "_fetch_current_stock_quotes",
        lambda *_: ({}, "eastmoney-test-quotes"),
    )

    result = provider.limit_up_atmosphere(
        trade_date="2026-07-14", force_refresh=True
    )

    assert result.decision == "CAUTION"
    assert "昨日涨停次日开盘样本" in result.missing_data
    assert "昨日涨停次日溢价样本" in result.missing_data
    assert any("结论已降级" in item for item in result.risks)


def test_limit_up_atmosphere_api_returns_explicit_decision(client, monkeypatch):
    from app.api.routes import market as market_routes

    result = LimitUpAtmosphereOut(
        source="eastmoney-test",
        trade_date="2026-07-14",
        previous_trade_date="2026-07-13",
        updated_at=datetime.now(),
        decision="FORBID",
        decision_label="禁止打板",
        score=-5,
        data_quality="完整",
        metrics=LimitUpAtmosphereMetrics(
            limit_up_count=8,
            limit_down_count=30,
            broken_count=12,
            seal_rate=40,
            break_rate=60,
            highest_board=2,
        ),
        evidence=["封板率 40.0%。"],
        risks=["炸板率偏高。"],
        missing_data=[],
        notes=[],
    )
    monkeypatch.setattr(market_routes.market_provider, "limit_up_atmosphere", lambda **_: result)

    response = client.post("/api/market/limit-up-atmosphere/refresh")

    assert response.status_code == 200
    payload = response.json()
    assert payload["decision"] == "FORBID"
    assert payload["metrics"]["break_rate"] == 60


def test_theme_identity_marks_same_level_competition_without_intent_claim():
    provider = MarketDataProvider()
    first = _stock("600010", "竞争甲", 3)
    second = _stock("600011", "竞争乙", 3)
    first.first_limit_time = "09:35:00"
    second.first_limit_time = "09:36:00"
    first.amount = 12
    second.amount = 11
    first.sealed_amount = 1.2
    second.sealed_amount = 1.1

    roles = provider._build_limit_up_identity_roles(
        [first, second], theme_highest=3, global_highest=3
    )

    assert all("同身位卡位竞争" in item.roles for item in roles)
    assert all("规则计算" in item.reason for item in roles)
    assert all("主力" not in item.reason for item in roles)


def test_non_mainline_theme_never_opens_limit_up_position():
    provider = MarketDataProvider()
    context = provider._build_limit_up_mainline_context(
        radar_theme=_theme_radar(
            rank=12,
            score=55,
            stage="发酵",
            net_inflow=2,
            main_inflow=0.5,
        ).themes[0],
        atmosphere_decision="ALLOW",
        ladder_count=3,
        completeness_score=82,
        completeness_label="多层梯队已成形",
    )

    assert context["mainline_level"] == "非主线题材"
    assert context["is_mainline"] is False
    assert context["max_position_ratio"] == 0
    assert "只观察" in context["stage_position_rule"]


def test_mainline_climax_or_ebb_forces_zero_position():
    provider = MarketDataProvider()
    for stage in ("高潮", "退潮"):
        context = provider._build_limit_up_mainline_context(
            radar_theme=_theme_radar(stage=stage).themes[0],
            atmosphere_decision="ALLOW",
            ladder_count=4,
            completeness_score=88,
            completeness_label="多层梯队已成形",
        )

        assert context["max_position_ratio"] == 0
        assert "禁止" in context["stage_position_rule"] or "只观察" in context["stage_position_rule"]


def test_highest_board_identity_is_dynamic_not_stock_name_hardcoded():
    provider = MarketDataProvider()
    leader = _stock("600664", "测试医药龙头", 6)
    follower = _stock("600665", "测试医药助攻", 2)
    context = provider._build_limit_up_mainline_context(
        radar_theme=_theme_radar(stage="发酵").themes[0],
        atmosphere_decision="ALLOW",
        ladder_count=4,
        completeness_score=85,
        completeness_label="多层梯队已成形",
    )

    roles = provider._build_limit_up_identity_roles(
        [leader, follower],
        theme_highest=6,
        global_highest=6,
        mainline_context=context,
    )

    leader_role = next(item for item in roles if item.code == leader.code)
    assert "全场最高标" in leader_role.roles
    assert "题材最高标" in leader_role.roles
    assert leader_role.max_position_ratio == 0.1
    assert "身份" in leader_role.persistence_basis[2]


def test_zero_low_open_ratio_is_treated_as_real_positive_evidence():
    provider = MarketDataProvider()
    metrics = LimitUpAtmosphereMetrics(
        limit_up_count=12,
        highest_board=3,
        next_day_open_sample_count=12,
        next_day_average_open_pct=1.5,
        next_day_low_open_ratio=0,
    )

    score, _, _, evidence, _ = provider._score_limit_up_atmosphere(metrics, [])

    assert score == 2
    assert any("低开比例 0.0%" in item for item in evidence)
