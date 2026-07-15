from sqlalchemy.orm import Session

from app.core.trading_clock import shanghai_day_bounds_utc_naive, shanghai_today
from app.models.trading import AccountDailyRisk, AccountState, PositionExecutionState, TradeLog
from app.schemas.trading import AccountRiskIn, AccountRiskOut

RISK_STATES = {"REDUCE_REQUIRED", "EXIT_REQUIRED", "STOP_LOSS_WARNING", "EXPECTATION_INVALIDATED"}


def account_risk(db: Session, payload: AccountRiskIn | None = None) -> AccountRiskOut:
    today = shanghai_today()
    trade_date = today.isoformat()
    row = db.query(AccountDailyRisk).filter(AccountDailyRisk.trade_date == trade_date).first()
    account = db.get(AccountState, 1)
    current_default = float(account.total_asset if account else 0)
    if row is None:
        row = AccountDailyRisk(trade_date=trade_date, opening_asset=0, current_asset=current_default)
        db.add(row)
    if payload:
        if payload.opening_asset is not None:
            row.opening_asset = max(0, payload.opening_asset)
        if payload.current_asset is not None:
            row.current_asset = max(0, payload.current_asset)
    elif current_default > 0:
        row.current_asset = current_default
    db.commit()
    db.refresh(row)

    states = db.query(PositionExecutionState).filter(PositionExecutionState.trade_date == trade_date).all()
    latest_by_code = {}
    for state in sorted(states, key=lambda item: (item.updated_at, item.id), reverse=True):
        latest_by_code.setdefault(state.code, state)
    degraded = sum(item.state in RISK_STATES for item in latest_by_code.values())
    day_start, day_end = shanghai_day_bounds_utc_naive()
    trades = db.query(TradeLog).filter(
        TradeLog.traded_at >= day_start,
        TradeLog.traded_at < day_end,
    ).all()
    stop_count = sum("止损" in (item.reason or "") for item in trades)
    complete = row.opening_asset > 0 and row.current_asset > 0
    ratio = (row.current_asset / row.opening_asset - 1) * 100 if complete else 0
    evidence = []
    if complete:
        evidence.append(f"账户当日收益 {ratio:.2f}%（期初 {row.opening_asset:.2f}，当前 {row.current_asset:.2f}）。")
    else:
        evidence.append("尚未设置当日期初资产，不能计算账户日内风险。")
    if degraded:
        evidence.append(f"{degraded} 只持仓处于减仓、退出或止损风险状态。")
    if stop_count:
        evidence.append(f"当日已记录 {stop_count} 笔止损交易。")

    level = "UNKNOWN"
    allowed = False
    action = "设置当日期初资产后启用账户风险联动。"
    if complete:
        level, allowed, action = "NORMAL", True, "账户风险正常，仍按单笔风险预算执行。"
        if ratio <= -4:
            level, allowed, action = "FORCED_DEFENSE", False, "强制防守：停止新开仓，只保留最低风险仓位。"
        elif ratio <= -3 or degraded >= 3:
            level, allowed, action = "REDUCE_ALL", False, "账户风险过高：非核心持仓减半，禁止新开仓。"
        elif ratio <= -2 or stop_count >= 2 or degraded >= 2:
            level, allowed, action = "BLOCK_NEW", False, "停止新开仓，优先处理现有风险仓位。"
    return AccountRiskOut(
        trade_date=trade_date, opening_asset=row.opening_asset, current_asset=row.current_asset,
        daily_profit_ratio=round(ratio, 2), level=level, new_positions_allowed=allowed,
        recommended_action=action, degraded_position_count=degraded, stop_loss_count=stop_count,
        data_complete=complete, evidence=evidence, updated_at=row.updated_at,
    )
