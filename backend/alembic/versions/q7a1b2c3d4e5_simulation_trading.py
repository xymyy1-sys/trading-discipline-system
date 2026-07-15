"""point-in-time simulation trading ledger

Revision ID: q7a1b2c3d4e5
Revises: p6f0a1b2c3d4
"""

from alembic import op
import sqlalchemy as sa


revision = "q7a1b2c3d4e5"
down_revision = "p6f0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "simulation_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, server_default="模拟账户"),
        sa.Column("initial_cash", sa.Float(), nullable=False, server_default="1000000"),
        sa.Column("cash", sa.Float(), nullable=False, server_default="1000000"),
        sa.Column("commission_rate", sa.Float(), nullable=False, server_default="0.0003"),
        sa.Column("minimum_commission", sa.Float(), nullable=False, server_default="5"),
        sa.Column("stamp_tax_rate", sa.Float(), nullable=False, server_default="0.0005"),
        sa.Column("transfer_fee_rate", sa.Float(), nullable=False, server_default="0.00001"),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_simulation_accounts_status", "simulation_accounts", ["status"])

    op.create_table(
        "simulation_evidence_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(64), nullable=False, server_default=""),
        sa.Column("strategy_source", sa.String(32), nullable=False),
        sa.Column("trade_date", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("quote_time", sa.DateTime(), nullable=True),
        sa.Column("data_quality", sa.String(24), nullable=False, server_default="missing"),
        sa.Column("quote_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("market_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("sector_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("expectation_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("volume_price_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("source_versions_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("market_regime", sa.String(48), nullable=False, server_default="UNKNOWN"),
        sa.Column("expectation_gap_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expectation_gap_band", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("volume_price_state", sa.String(64), nullable=False, server_default=""),
        sa.Column("sector_state", sa.String(64), nullable=False, server_default=""),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.UniqueConstraint(
            "account_id", "code", "strategy_source", "trade_date", "version",
            name="uq_sim_evidence_version",
        ),
    )
    for column in (
        "account_id", "code", "strategy_source", "trade_date", "captured_at", "quote_time",
        "market_regime", "expectation_gap_score", "expectation_gap_band", "content_hash",
    ):
        op.create_index(f"ix_simulation_evidence_snapshots_{column}", "simulation_evidence_snapshots", [column])

    op.create_table(
        "simulation_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("decision_evidence_snapshot_id", sa.Integer(), nullable=False),
        sa.Column("strategy_source", sa.String(32), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(64), nullable=False, server_default=""),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("order_type", sa.String(16), nullable=False, server_default="MARKET"),
        sa.Column("limit_price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("filled_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("average_fill_price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("reject_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("client_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("trade_date", sa.String(16), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=False),
        sa.Column("last_evaluated_at", sa.DateTime(), nullable=False),
    )
    for column in (
        "account_id", "decision_evidence_snapshot_id", "strategy_source", "code", "side", "status",
        "trade_date", "submitted_at", "last_evaluated_at",
    ):
        op.create_index(f"ix_simulation_orders_{column}", "simulation_orders", [column])

    op.create_table(
        "simulation_fills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("fill_evidence_snapshot_id", sa.Integer(), nullable=False),
        sa.Column("strategy_source", sa.String(32), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(64), nullable=False, server_default=""),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("gross_amount", sa.Float(), nullable=False),
        sa.Column("commission", sa.Float(), nullable=False, server_default="0"),
        sa.Column("stamp_tax", sa.Float(), nullable=False, server_default="0"),
        sa.Column("transfer_fee", sa.Float(), nullable=False, server_default="0"),
        sa.Column("net_cash_flow", sa.Float(), nullable=False, server_default="0"),
        sa.Column("realized_pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("trade_date", sa.String(16), nullable=False),
        sa.Column("filled_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("order_id", name="uq_sim_fill_order"),
    )
    for column in (
        "order_id", "account_id", "fill_evidence_snapshot_id", "strategy_source", "code", "side",
        "trade_date", "filled_at",
    ):
        op.create_index(f"ix_simulation_fills_{column}", "simulation_fills", [column])

    op.create_table(
        "simulation_trade_lots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(64), nullable=False, server_default=""),
        sa.Column("entry_order_id", sa.Integer(), nullable=True),
        sa.Column("entry_fill_id", sa.Integer(), nullable=True),
        sa.Column("entry_decision_evidence_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("strategy_source", sa.String(32), nullable=False),
        sa.Column("initial_quantity", sa.Integer(), nullable=False),
        sa.Column("remaining_quantity", sa.Integer(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("entry_gross_amount", sa.Float(), nullable=False),
        sa.Column("entry_costs", sa.Float(), nullable=False, server_default="0"),
        sa.Column("exit_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exit_gross_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("exit_costs", sa.Float(), nullable=False, server_default="0"),
        sa.Column("realized_pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("opened_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("entry_order_id", name="uq_sim_trade_lot_entry_order"),
        sa.UniqueConstraint("entry_fill_id", name="uq_sim_trade_lot_entry_fill"),
    )
    for column in (
        "account_id", "code", "entry_order_id", "entry_fill_id",
        "entry_decision_evidence_snapshot_id", "strategy_source", "status", "opened_at", "closed_at",
    ):
        op.create_index(f"ix_simulation_trade_lots_{column}", "simulation_trade_lots", [column])

    op.create_table(
        "simulation_closed_trades",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(64), nullable=False, server_default=""),
        sa.Column("strategy_source", sa.String(32), nullable=False),
        sa.Column("entry_decision_evidence_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("entry_order_id", sa.Integer(), nullable=True),
        sa.Column("entry_fill_id", sa.Integer(), nullable=True),
        sa.Column("closing_order_id", sa.Integer(), nullable=False),
        sa.Column("closing_fill_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("entry_average_price", sa.Float(), nullable=False),
        sa.Column("exit_average_price", sa.Float(), nullable=False),
        sa.Column("entry_gross_amount", sa.Float(), nullable=False),
        sa.Column("exit_gross_amount", sa.Float(), nullable=False),
        sa.Column("total_costs", sa.Float(), nullable=False, server_default="0"),
        sa.Column("realized_pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("return_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("opened_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=False),
        sa.Column("holding_days", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("lot_id", name="uq_sim_closed_trade_lot"),
    )
    for column in (
        "account_id", "lot_id", "code", "strategy_source", "entry_decision_evidence_snapshot_id",
        "entry_order_id", "entry_fill_id", "closing_order_id", "closing_fill_id", "opened_at", "closed_at",
    ):
        op.create_index(f"ix_simulation_closed_trades_{column}", "simulation_closed_trades", [column])

    op.create_table(
        "simulation_positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(64), nullable=False, server_default=""),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("today_buy_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("average_cost", sa.Float(), nullable=False, server_default="0"),
        sa.Column("market_price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("market_value", sa.Float(), nullable=False, server_default="0"),
        sa.Column("unrealized_pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("realized_pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("last_rollover_date", sa.String(16), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("account_id", "code", name="uq_sim_position_account_code"),
    )
    for column in ("account_id", "code", "last_rollover_date"):
        op.create_index(f"ix_simulation_positions_{column}", "simulation_positions", [column])

    op.create_table(
        "simulation_daily_equity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("trade_date", sa.String(16), nullable=False),
        sa.Column("cash", sa.Float(), nullable=False, server_default="0"),
        sa.Column("market_value", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_equity", sa.Float(), nullable=False, server_default="0"),
        sa.Column("daily_pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("return_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("drawdown_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("account_id", "trade_date", name="uq_sim_equity_account_date"),
    )
    for column in ("account_id", "trade_date", "captured_at"):
        op.create_index(f"ix_simulation_daily_equity_{column}", "simulation_daily_equity", [column])


def downgrade() -> None:
    op.drop_table("simulation_daily_equity")
    op.drop_table("simulation_positions")
    op.drop_table("simulation_closed_trades")
    op.drop_table("simulation_trade_lots")
    op.drop_table("simulation_fills")
    op.drop_table("simulation_orders")
    op.drop_table("simulation_evidence_snapshots")
    op.drop_table("simulation_accounts")
