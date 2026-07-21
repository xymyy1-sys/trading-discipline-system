"""preserve immutable intraday sector-state samples

Revision ID: x4h8i9j0k1l2
Revises: w3g7h8i9j0k1
"""

from alembic import op
import sqlalchemy as sa


revision = "x4h8i9j0k1l2"
down_revision = "w3g7h8i9j0k1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sector_crowding_snapshot_samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trade_date", sa.String(16), nullable=False),
        sa.Column("board_type", sa.String(16), nullable=False, server_default="行业"),
        sa.Column("board_key", sa.String(160), nullable=False),
        sa.Column("board_code", sa.String(32), nullable=False, server_default=""),
        sa.Column("board_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("provider_updated_at", sa.DateTime(), nullable=True),
        sa.Column("source", sa.String(512), nullable=False, server_default=""),
        sa.Column("data_quality", sa.String(24), nullable=False, server_default="missing"),
        sa.Column("status", sa.String(64), nullable=False, server_default="数据不足"),
        sa.Column("risk_level", sa.String(16), nullable=False, server_default="UNKNOWN"),
        sa.Column("distribution_state", sa.String(48), nullable=False, server_default="数据不足"),
        sa.Column("instantaneous_distribution_state", sa.String(48), nullable=False, server_default="数据不足"),
        sa.Column("distribution_risk_level", sa.String(16), nullable=False, server_default="UNKNOWN"),
        sa.Column("distribution_risk_score", sa.Float(), nullable=True),
        sa.Column("distribution_confirmation_count", sa.Integer(), nullable=True),
        sa.Column("change_pct", sa.Float(), nullable=True),
        sa.Column("net_inflow", sa.Float(), nullable=True),
        sa.Column("flow_speed", sa.Float(), nullable=True),
        sa.Column("flow_acceleration", sa.Float(), nullable=True),
        sa.Column("flow_turning", sa.String(48), nullable=False, server_default=""),
        sa.Column("financing_balance", sa.Float(), nullable=True),
        sa.Column("financing_net_buy", sa.Float(), nullable=True),
        sa.Column("margin_as_of", sa.String(16), nullable=False, server_default=""),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("counter_evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("actions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("raw_payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "trade_date",
            "board_type",
            "board_key",
            "payload_hash",
            name="uq_sector_crowding_sample_payload",
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
        "status",
        "risk_level",
        "distribution_state",
        "instantaneous_distribution_state",
        "distribution_risk_level",
        "payload_hash",
    ):
        op.create_index(
            f"ix_sector_crowding_snapshot_samples_{column}",
            "sector_crowding_snapshot_samples",
            [column],
        )


def downgrade() -> None:
    for column in (
        "payload_hash",
        "distribution_risk_level",
        "instantaneous_distribution_state",
        "distribution_state",
        "risk_level",
        "status",
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
            f"ix_sector_crowding_snapshot_samples_{column}",
            table_name="sector_crowding_snapshot_samples",
        )
    op.drop_table("sector_crowding_snapshot_samples")
