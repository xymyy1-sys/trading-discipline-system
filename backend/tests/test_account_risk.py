from datetime import datetime

from app.models.trading import PositionExecutionState, TradeLog
from app.schemas.trading import AccountRiskIn
from app.services.account_risk import account_risk


def test_account_risk_blocks_new_positions_at_two_percent_loss(db_session):
    result = account_risk(db_session, AccountRiskIn(opening_asset=100000, current_asset=97900))
    assert result.level == "BLOCK_NEW"
    assert result.new_positions_allowed is False
    assert result.daily_profit_ratio == -2.1


def test_account_risk_escalates_for_multiple_degraded_positions(db_session):
    for index in range(3):
        db_session.add(PositionExecutionState(
            holding_id=index + 1, code=f"60000{index}", name="risk", trade_date=datetime.now().date().isoformat(),
            state="REDUCE_REQUIRED", updated_at=datetime.now(),
        ))
    db_session.commit()
    result = account_risk(db_session, AccountRiskIn(opening_asset=100000, current_asset=99500))
    assert result.level == "REDUCE_ALL"
    assert result.degraded_position_count == 3


def test_account_risk_requires_opening_asset(client):
    response = client.get("/api/account/risk")
    assert response.status_code == 200
    assert response.json()["data_complete"] is False
