"""persist sector crowding and global evidence history

Revision ID: w3g7h8i9j0k1
Revises: v2f6g7h8i9j0
"""

from alembic import op
import sqlalchemy as sa


revision = "w3g7h8i9j0k1"
down_revision = "v2f6g7h8i9j0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sector_crowding_daily_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trade_date", sa.String(16), nullable=False),
        sa.Column("board_type", sa.String(16), nullable=False, server_default="行业"),
        sa.Column("board_key", sa.String(160), nullable=False),
        sa.Column("board_code", sa.String(32), nullable=False, server_default=""),
        sa.Column("board_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(512), nullable=False, server_default=""),
        sa.Column("data_quality", sa.String(24), nullable=False, server_default="missing"),
        sa.Column("provider_trade_date", sa.String(16), nullable=False, server_default=""),
        sa.Column("provider_updated_at", sa.DateTime(), nullable=True),
        sa.Column("heat_score", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(64), nullable=False, server_default="数据不足"),
        sa.Column("risk_level", sa.String(16), nullable=False, server_default="UNKNOWN"),
        sa.Column("trend_score", sa.Float(), nullable=True),
        sa.Column("flow_score", sa.Float(), nullable=True),
        sa.Column("crowding_score", sa.Float(), nullable=True),
        sa.Column("margin_score", sa.Float(), nullable=True),
        sa.Column("attention_score", sa.Float(), nullable=True),
        sa.Column("change_pct", sa.Float(), nullable=True),
        sa.Column("change_pct_5d", sa.Float(), nullable=True),
        sa.Column("change_pct_10d", sa.Float(), nullable=True),
        sa.Column("net_inflow", sa.Float(), nullable=True),
        sa.Column("net_inflow_5d", sa.Float(), nullable=True),
        sa.Column("net_inflow_10d", sa.Float(), nullable=True),
        sa.Column("flow_speed", sa.Float(), nullable=True),
        sa.Column("flow_acceleration", sa.Float(), nullable=True),
        sa.Column("flow_turning", sa.String(48), nullable=False, server_default=""),
        sa.Column("limit_up_count", sa.Integer(), nullable=True),
        sa.Column("financing_balance", sa.Float(), nullable=True),
        sa.Column("financing_net_buy", sa.Float(), nullable=True),
        sa.Column("financing_balance_ratio", sa.Float(), nullable=True),
        sa.Column("financing_net_buy_5d", sa.Float(), nullable=True),
        sa.Column("financing_net_buy_10d", sa.Float(), nullable=True),
        sa.Column("financing_net_buy_20d", sa.Float(), nullable=True),
        sa.Column("margin_as_of", sa.String(16), nullable=False, server_default=""),
        sa.Column("margin_realtime", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("distribution_state", sa.String(48), nullable=False, server_default="数据不足"),
        sa.Column("distribution_risk_level", sa.String(16), nullable=False, server_default="UNKNOWN"),
        sa.Column("distribution_risk_score", sa.Float(), nullable=True),
        sa.Column("order_flow_exhausted", sa.Boolean(), nullable=True),
        sa.Column("leverage_crowding", sa.Boolean(), nullable=True),
        sa.Column("price_response_weak", sa.Boolean(), nullable=True),
        sa.Column("distribution_confirmation_count", sa.Integer(), nullable=True),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("counter_evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("actions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("distribution_evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("distribution_counter_evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("distribution_actions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("raw_payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("payload_hash", sa.String(64), nullable=False, server_default=""),
        # Application writes all timestamps as Shanghai wall-clock naive values;
        # a database CURRENT_TIMESTAMP default would silently introduce UTC on
        # SQLite/PostgreSQL and break ordering around the local date boundary.
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "trade_date",
            "board_type",
            "board_key",
            name="uq_sector_crowding_daily_board",
        ),
    )
    for column in (
        "id",
        "trade_date",
        "board_type",
        "board_key",
        "board_code",
        "board_name",
        "captured_at",
        "data_quality",
        "provider_trade_date",
        "status",
        "risk_level",
        "distribution_state",
        "distribution_risk_level",
        "payload_hash",
    ):
        op.create_index(
            f"ix_sector_crowding_daily_snapshots_{column}",
            "sector_crowding_daily_snapshots",
            [column],
        )

    op.create_table(
        "global_evidence_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trade_date", sa.String(16), nullable=False, server_default=""),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("as_of", sa.String(64), nullable=False, server_default=""),
        sa.Column("source", sa.String(512), nullable=False, server_default=""),
        sa.Column("data_quality", sa.String(24), nullable=False, server_default="missing"),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.UniqueConstraint(
            "trade_date",
            "payload_hash",
            name="uq_global_evidence_trade_date_payload_hash",
        ),
    )
    for column in ("id", "trade_date", "captured_at", "as_of", "data_quality", "payload_hash"):
        op.create_index(
            f"ix_global_evidence_snapshots_{column}",
            "global_evidence_snapshots",
            [column],
        )


def downgrade() -> None:
    for column in ("payload_hash", "data_quality", "as_of", "captured_at", "trade_date", "id"):
        op.drop_index(f"ix_global_evidence_snapshots_{column}", table_name="global_evidence_snapshots")
    op.drop_table("global_evidence_snapshots")

    for column in (
        "payload_hash",
        "distribution_risk_level",
        "distribution_state",
        "risk_level",
        "status",
        "provider_trade_date",
        "data_quality",
        "captured_at",
        "board_name",
        "board_code",
        "board_key",
        "board_type",
        "trade_date",
        "id",
    ):
        op.drop_index(
            f"ix_sector_crowding_daily_snapshots_{column}",
            table_name="sector_crowding_daily_snapshots",
        )
    op.drop_table("sector_crowding_daily_snapshots")
