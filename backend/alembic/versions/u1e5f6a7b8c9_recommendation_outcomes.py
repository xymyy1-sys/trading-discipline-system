"""post-hoc recommendation outcome ledger

Revision ID: u1e5f6a7b8c9
Revises: t0d4e5f6a7b8
"""

from alembic import op
import sqlalchemy as sa


revision = "u1e5f6a7b8c9"
down_revision = "t0d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recommendation_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_key", sa.String(96), nullable=False),
        sa.Column("recommendation_id", sa.Integer(), nullable=False),
        sa.Column("recommendation_revision_id", sa.Integer(), nullable=True),
        sa.Column("trade_date", sa.String(16), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(64), nullable=False, server_default=""),
        sa.Column("signal_at", sa.DateTime(), nullable=False),
        sa.Column("level", sa.String(24), nullable=False, server_default="INFO"),
        sa.Column("state", sa.String(48), nullable=False, server_default=""),
        sa.Column("action", sa.String(64), nullable=False, server_default=""),
        sa.Column("recommended_ratio", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reference_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("reference_at", sa.DateTime(), nullable=True),
        sa.Column("reference_latency_seconds", sa.Float(), nullable=True),
        sa.Column("reference_price", sa.Float(), nullable=True),
        sa.Column("reference_source", sa.String(128), nullable=False, server_default=""),
        sa.Column("reference_quality", sa.String(32), nullable=False, server_default=""),
        sa.Column("price_5m", sa.Float(), nullable=True),
        sa.Column("return_5m_pct", sa.Float(), nullable=True),
        sa.Column("price_15m", sa.Float(), nullable=True),
        sa.Column("return_15m_pct", sa.Float(), nullable=True),
        sa.Column("price_30m", sa.Float(), nullable=True),
        sa.Column("return_30m_pct", sa.Float(), nullable=True),
        sa.Column("close_price", sa.Float(), nullable=True),
        sa.Column("return_close_pct", sa.Float(), nullable=True),
        sa.Column("next_trade_date", sa.String(16), nullable=True),
        sa.Column("next_open_price", sa.Float(), nullable=True),
        sa.Column("return_next_open_pct", sa.Float(), nullable=True),
        sa.Column("next_close_price", sa.Float(), nullable=True),
        sa.Column("return_next_close_pct", sa.Float(), nullable=True),
        sa.Column("mfe_pct", sa.Float(), nullable=True),
        sa.Column("mae_pct", sa.Float(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("data_quality", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("invalid_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("missing_horizons_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("evaluated_through_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for column in (
        "id",
        "source_key",
        "recommendation_id",
        "recommendation_revision_id",
        "trade_date",
        "code",
        "signal_at",
        "reference_snapshot_id",
        "next_trade_date",
        "status",
        "data_quality",
        "created_at",
    ):
        op.create_index(
            f"ix_recommendation_outcomes_{column}",
            "recommendation_outcomes",
            [column],
            unique=column == "source_key",
        )


def downgrade() -> None:
    op.drop_table("recommendation_outcomes")
