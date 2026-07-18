from datetime import datetime

from app.models.trading import (
    ActionRecommendation,
    ActionRecommendationRevision,
    RecommendationOutcome,
    VolumePriceSnapshot,
)
from app.services.recommendation_outcomes import (
    _session_target,
    recommendation_outcome_summary,
    refresh_recommendation_outcomes,
    unresolved_outcome_targets,
)


def _snapshot(
    trade_date: str,
    captured_at: datetime,
    price: float,
    *,
    quality: str = "realtime",
    open_price: float = 0,
) -> VolumePriceSnapshot:
    return VolumePriceSnapshot(
        trade_date=trade_date,
        code="600001",
        name="测试股票",
        stage="盘中",
        captured_at=captured_at,
        price=price,
        open_price=open_price,
        data_quality=quality,
        data_source="test-feed",
    )


def _recommendation_with_revision(db_session, signal_at: datetime) -> ActionRecommendation:
    row = ActionRecommendation(
        trade_date=signal_at.date().isoformat(),
        code="600001",
        name="测试股票",
        created_at=signal_at,
        level="WARNING",
        state="WEAKENING",
        action="减仓25%",
        recommended_ratio=0.25,
    )
    db_session.add(row)
    db_session.flush()
    db_session.add(
        ActionRecommendationRevision(
            recommendation_id=row.id,
            version=1,
            level=row.level,
            state=row.state,
            action=row.action,
            recommended_ratio=row.recommended_ratio,
            created_at=signal_at,
        )
    )
    db_session.commit()
    return row


def test_refresh_builds_complete_revision_outcome_without_future_leakage(db_session):
    signal_at = datetime(2026, 7, 15, 10, 0)
    recommendation = _recommendation_with_revision(db_session, signal_at)
    db_session.add_all(
        [
            _snapshot("2026-07-15", signal_at, 100),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 10, 5), 101),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 10, 15), 98),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 10, 30), 103),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 14, 55), 99),
            _snapshot("2026-07-16", datetime(2026, 7, 16, 9, 30), 97, open_price=96),
            _snapshot("2026-07-16", datetime(2026, 7, 16, 14, 55), 95),
        ]
    )
    db_session.commit()

    result = refresh_recommendation_outcomes(
        db_session,
        now=datetime(2026, 7, 16, 15, 10),
    )

    assert result["created"] == 1
    row = db_session.query(RecommendationOutcome).one()
    assert row.recommendation_id == recommendation.id
    assert row.recommendation_revision_id is not None
    assert row.source_key.endswith(f"revision:{row.recommendation_revision_id}")
    assert row.status == "complete"
    assert row.data_quality == "reliable"
    assert row.reference_at == signal_at
    assert row.reference_latency_seconds == 0
    assert row.reference_price == 100
    assert row.return_5m_pct == 1
    assert row.return_15m_pct == -2
    assert row.return_30m_pct == 3
    assert row.return_close_pct == -1
    # The official open field, not a later quote, is used for next open.
    assert row.next_open_price == 96
    assert row.return_next_open_pct == -4
    assert row.return_next_close_pct == -5
    assert row.mfe_pct == 3
    assert row.mae_pct == -5


def test_horizons_are_anchored_to_signal_time_not_reference_snapshot(db_session):
    signal_at = datetime(2026, 7, 15, 10, 5)
    _recommendation_with_revision(db_session, signal_at)
    db_session.add_all(
        [
            _snapshot("2026-07-15", datetime(2026, 7, 15, 10, 3), 100),
            # This is reference+5m, but not signal+5m and must be ignored.
            _snapshot("2026-07-15", datetime(2026, 7, 15, 10, 8), 90),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 10, 10), 110),
        ]
    )
    db_session.commit()

    refresh_recommendation_outcomes(db_session, now=datetime(2026, 7, 15, 10, 12))

    row = db_session.query(RecommendationOutcome).one()
    assert row.reference_at == datetime(2026, 7, 15, 10, 3)
    assert row.reference_latency_seconds == -120
    assert row.price_5m == 110
    assert row.return_5m_pct == 10


def test_next_trade_date_ignores_manual_and_degraded_snapshot_days(db_session):
    signal_at = datetime(2026, 7, 15, 10, 0)
    _recommendation_with_revision(db_session, signal_at)
    db_session.add_all(
        [
            _snapshot("2026-07-15", signal_at, 100),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 10, 5), 101),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 10, 15), 102),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 10, 30), 103),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 14, 55), 104),
            _snapshot("2026-07-16", datetime(2026, 7, 16, 9, 30), 80, quality="manual"),
            _snapshot("2026-07-16", datetime(2026, 7, 16, 14, 55), 81, quality="degraded"),
            _snapshot("2026-07-17", datetime(2026, 7, 17, 9, 30), 105, open_price=105),
            _snapshot("2026-07-17", datetime(2026, 7, 17, 14, 55), 106),
        ]
    )
    db_session.commit()

    refresh_recommendation_outcomes(db_session, now=datetime(2026, 7, 17, 15, 10))

    row = db_session.query(RecommendationOutcome).one()
    assert row.next_trade_date == "2026-07-17"
    assert row.next_open_price == 105
    assert row.next_close_price == 106
    assert row.status == "complete"


def test_no_reliable_next_trade_date_stays_unresolved(db_session):
    signal_at = datetime(2026, 7, 15, 10, 0)
    _recommendation_with_revision(db_session, signal_at)
    db_session.add_all(
        [
            _snapshot("2026-07-15", signal_at, 100),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 10, 5), 101),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 10, 15), 102),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 10, 30), 103),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 14, 55), 104),
            _snapshot("2026-07-16", datetime(2026, 7, 16, 9, 30), 99, quality="manual"),
        ]
    )
    db_session.commit()

    refresh_recommendation_outcomes(db_session, now=datetime(2026, 7, 17, 15, 10))

    row = db_session.query(RecommendationOutcome).one()
    assert row.next_trade_date is None
    assert row.status == "partial"
    assert row.data_quality == "reliable"


def test_missing_reliable_next_session_expires_after_fifteen_days(db_session):
    signal_at = datetime(2026, 7, 1, 10, 0)
    _recommendation_with_revision(db_session, signal_at)
    db_session.add_all(
        [
            _snapshot("2026-07-01", signal_at, 100),
            _snapshot("2026-07-01", datetime(2026, 7, 1, 10, 5), 101),
            _snapshot("2026-07-01", datetime(2026, 7, 1, 10, 15), 102),
            _snapshot("2026-07-01", datetime(2026, 7, 1, 10, 30), 103),
            _snapshot("2026-07-01", datetime(2026, 7, 1, 14, 55), 104),
        ]
    )
    db_session.commit()

    refresh_recommendation_outcomes(db_session, now=datetime(2026, 7, 1, 15, 10))
    assert db_session.query(RecommendationOutcome).one().status == "partial"

    refresh_recommendation_outcomes(db_session, now=datetime(2026, 7, 18, 10, 0))

    row = db_session.query(RecommendationOutcome).one()
    assert row.status == "invalid"
    assert row.data_quality == "invalid"
    assert "15 天内没有" in row.invalid_reason


def test_next_open_prefers_late_reliable_snapshot_with_official_open(db_session):
    signal_at = datetime(2026, 7, 15, 10, 0)
    _recommendation_with_revision(db_session, signal_at)
    db_session.add_all(
        [
            _snapshot("2026-07-15", signal_at, 100),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 10, 5), 101),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 10, 15), 102),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 10, 30), 103),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 14, 55), 104),
            _snapshot("2026-07-16", datetime(2026, 7, 16, 9, 30), 97),
            _snapshot("2026-07-16", datetime(2026, 7, 16, 10, 0), 98, open_price=96),
            _snapshot("2026-07-16", datetime(2026, 7, 16, 14, 55), 99),
        ]
    )
    db_session.commit()

    refresh_recommendation_outcomes(db_session, now=datetime(2026, 7, 16, 15, 10))

    row = db_session.query(RecommendationOutcome).one()
    assert row.next_open_price == 96
    assert row.return_next_open_pct == -4


def test_revision_key_prevents_minute_refresh_from_overwriting_signal_history(db_session):
    first_at = datetime(2026, 7, 15, 10, 0)
    recommendation = _recommendation_with_revision(db_session, first_at)
    db_session.add(_snapshot("2026-07-15", first_at, 10))
    db_session.commit()

    refresh_recommendation_outcomes(db_session, now=datetime(2026, 7, 15, 10, 1))
    recommendation.created_at = datetime(2026, 7, 15, 10, 2)
    db_session.commit()
    refresh_recommendation_outcomes(db_session, now=datetime(2026, 7, 15, 10, 3))

    assert db_session.query(RecommendationOutcome).count() == 1
    first = db_session.query(RecommendationOutcome).one()
    assert first.signal_at == first_at

    second_at = datetime(2026, 7, 15, 10, 10)
    db_session.add(
        ActionRecommendationRevision(
            recommendation_id=recommendation.id,
            version=2,
            level="CRITICAL",
            state="INVALIDATED",
            action="全部退出",
            recommended_ratio=1,
            created_at=second_at,
        )
    )
    db_session.add(_snapshot("2026-07-15", second_at, 9.8))
    db_session.commit()
    refresh_recommendation_outcomes(db_session, now=datetime(2026, 7, 15, 10, 11))

    assert db_session.query(RecommendationOutcome).count() == 2
    assert {row.signal_at for row in db_session.query(RecommendationOutcome).all()} == {
        first_at,
        second_at,
    }


def test_intraday_horizon_uses_trading_minutes_across_lunch_break():
    assert _session_target(datetime(2026, 7, 15, 11, 29), 5) == datetime(2026, 7, 15, 13, 4)


def test_late_signal_marks_unavailable_intraday_horizons_but_can_complete(db_session):
    signal_at = datetime(2026, 7, 15, 14, 50)
    _recommendation_with_revision(db_session, signal_at)
    db_session.add_all(
        [
            _snapshot("2026-07-15", signal_at, 20),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 14, 55), 19.8),
            _snapshot("2026-07-16", datetime(2026, 7, 16, 9, 30), 19.5, open_price=19.4),
            _snapshot("2026-07-16", datetime(2026, 7, 16, 14, 55), 21),
        ]
    )
    db_session.commit()

    refresh_recommendation_outcomes(db_session, now=datetime(2026, 7, 16, 15, 10))

    row = db_session.query(RecommendationOutcome).one()
    assert row.status == "complete"
    assert row.price_5m == 19.8
    assert row.price_15m is None
    assert row.price_30m is None
    assert "15m:建议时点过晚" in row.missing_horizons_json
    assert "30m:建议时点过晚" in row.missing_horizons_json


def test_same_day_close_never_reuses_a_quote_at_or_before_the_signal(db_session):
    signal_at = datetime(2026, 7, 15, 14, 58)
    _recommendation_with_revision(db_session, signal_at)
    db_session.add_all(
        [
            _snapshot("2026-07-15", datetime(2026, 7, 15, 14, 55), 20.2),
            _snapshot("2026-07-15", signal_at, 20),
            _snapshot("2026-07-16", datetime(2026, 7, 16, 9, 30), 19.8, open_price=19.8),
            _snapshot("2026-07-16", datetime(2026, 7, 16, 14, 55), 20.5),
        ]
    )
    db_session.commit()

    refresh_recommendation_outcomes(db_session, now=datetime(2026, 7, 16, 15, 10))

    row = db_session.query(RecommendationOutcome).one()
    assert row.reference_at == signal_at
    assert row.close_price is None
    assert '"close"' in row.missing_horizons_json
    assert row.status == "invalid"


def test_manual_reference_is_invalid_after_session(db_session):
    signal_at = datetime(2026, 7, 15, 10, 0)
    db_session.add(
        ActionRecommendation(
            trade_date="2026-07-15",
            code="600001",
            name="测试股票",
            created_at=signal_at,
            action="观察",
        )
    )
    db_session.add(_snapshot("2026-07-15", signal_at, 10, quality="manual"))
    db_session.commit()

    refresh_recommendation_outcomes(db_session, now=datetime(2026, 7, 16, 10, 0))

    row = db_session.query(RecommendationOutcome).one()
    assert row.status == "invalid"
    assert row.data_quality == "invalid"
    assert "手工" in row.invalid_reason


def test_partial_outcome_is_revisited_and_target_is_tracked(db_session):
    signal_at = datetime(2026, 7, 15, 10, 0)
    _recommendation_with_revision(db_session, signal_at)
    db_session.add_all(
        [
            _snapshot("2026-07-15", signal_at, 10),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 10, 5), 10.1),
        ]
    )
    db_session.commit()

    refresh_recommendation_outcomes(db_session, now=datetime(2026, 7, 15, 10, 10))

    row = db_session.query(RecommendationOutcome).one()
    assert row.status == "partial"
    assert unresolved_outcome_targets(db_session, now=datetime(2026, 7, 15, 10, 10)) == [
        ("600001", "测试股票")
    ]


def test_unresolved_target_default_lookback_covers_fifteen_calendar_days(db_session):
    signal_at = datetime(2026, 7, 3, 10, 0)
    db_session.add(
        RecommendationOutcome(
            source_key="recommendation:999:base",
            recommendation_id=999,
            trade_date="2026-07-03",
            code="600001",
            name="历史待评估标的",
            signal_at=signal_at,
            status="pending",
            data_quality="pending",
            created_at=signal_at,
            updated_at=signal_at,
        )
    )
    db_session.commit()

    assert unresolved_outcome_targets(db_session, now=datetime(2026, 7, 18, 10, 0)) == [
        ("600001", "历史待评估标的")
    ]


def test_refresh_rotates_through_pending_backlog_instead_of_starving_old_rows(db_session):
    for index in range(3):
        signal_at = datetime(2026, 7, 18, 9, 30 + index)
        db_session.add(
            RecommendationOutcome(
                source_key=f"rotation:{index}",
                recommendation_id=100 + index,
                trade_date="2026-07-18",
                code=f"60000{index + 1}",
                name=f"轮转标的{index + 1}",
                signal_at=signal_at,
                status="pending",
                data_quality="pending",
                created_at=signal_at,
                updated_at=signal_at,
            )
        )
    db_session.commit()

    for minute in range(3):
        refresh_recommendation_outcomes(
            db_session,
            now=datetime(2026, 7, 18, 10, minute),
            limit=1,
        )

    assert all(
        row.updated_at >= datetime(2026, 7, 18, 10, 0)
        for row in db_session.query(RecommendationOutcome).all()
    )


def test_unresolved_targets_group_revisions_before_applying_stock_limit(db_session):
    for index in range(40):
        signal_at = datetime(2026, 7, 18, 10, index % 60)
        db_session.add(
            RecommendationOutcome(
                source_key=f"crowded:{index}",
                recommendation_id=200,
                recommendation_revision_id=1000 + index,
                trade_date="2026-07-18",
                code="600001",
                name="多版本标的",
                signal_at=signal_at,
                status="partial",
                data_quality="reliable",
                created_at=signal_at,
                updated_at=signal_at,
            )
        )
    other_at = datetime(2026, 7, 18, 9, 45)
    db_session.add(
        RecommendationOutcome(
            source_key="other-stock",
            recommendation_id=201,
            trade_date="2026-07-18",
            code="600002",
            name="另一卖出标的",
            signal_at=other_at,
            status="partial",
            data_quality="reliable",
            created_at=other_at,
            updated_at=other_at,
        )
    )
    db_session.commit()

    assert unresolved_outcome_targets(
        db_session,
        now=datetime(2026, 7, 18, 11, 0),
        limit=2,
    ) == [("600001", "多版本标的"), ("600002", "另一卖出标的")]


def test_summary_averages_only_complete_reliable_outcomes(db_session):
    signal_at = datetime(2026, 7, 15, 10, 0)
    db_session.add_all(
        [
            RecommendationOutcome(
                source_key="summary:reliable",
                recommendation_id=1,
                trade_date="2026-07-15",
                code="600001",
                signal_at=signal_at,
                status="complete",
                data_quality="reliable",
                return_5m_pct=1,
                mfe_pct=2,
                mae_pct=-1,
            ),
            RecommendationOutcome(
                source_key="summary:degraded",
                recommendation_id=2,
                trade_date="2026-07-15",
                code="600002",
                signal_at=signal_at,
                status="complete",
                data_quality="degraded",
                return_5m_pct=99,
                mfe_pct=99,
                mae_pct=-99,
            ),
            RecommendationOutcome(
                source_key="summary:partial",
                recommendation_id=3,
                trade_date="2026-07-15",
                code="600003",
                signal_at=signal_at,
                status="partial",
                data_quality="reliable",
                return_5m_pct=50,
                mfe_pct=50,
                mae_pct=-50,
            ),
        ]
    )
    db_session.commit()

    summary = recommendation_outcome_summary(db_session)

    assert summary["average_returns"]["5m"] == 1
    assert summary["average_returns"]["mfe"] == 2
    assert summary["average_returns"]["mae"] == -1
    assert "采样区间最高涨幅和最低跌幅" in summary["note"]


def test_outcome_api_lists_and_summarizes_objective_returns(client, db_session):
    signal_at = datetime(2026, 7, 15, 10, 0)
    _recommendation_with_revision(db_session, signal_at)
    db_session.add_all(
        [
            _snapshot("2026-07-15", signal_at, 100),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 10, 5), 101),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 10, 15), 102),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 10, 30), 103),
            _snapshot("2026-07-15", datetime(2026, 7, 15, 14, 55), 104),
            _snapshot("2026-07-16", datetime(2026, 7, 16, 9, 30), 105, open_price=105),
            _snapshot("2026-07-16", datetime(2026, 7, 16, 14, 55), 106),
        ]
    )
    db_session.commit()
    refresh_recommendation_outcomes(db_session, now=datetime(2026, 7, 16, 15, 10))

    listing = client.get("/api/reviews/recommendation-outcomes?status=complete")
    summary = client.get("/api/reviews/recommendation-outcomes/summary")

    assert listing.status_code == 200
    assert listing.json()[0]["missing_horizons"] == []
    assert listing.json()[0]["return_next_close_pct"] == 6
    assert summary.status_code == 200
    payload = summary.json()
    assert payload["status_counts"]["complete"] == 1
    assert payload["complete_coverage_pct"] == 100
    assert payload["average_returns"]["next_close"] == 6
    assert "不等同于规则胜率" in payload["note"]
