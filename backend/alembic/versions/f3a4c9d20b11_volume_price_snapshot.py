"""volume price snapshot

Revision ID: f3a4c9d20b11
Revises: c58d7e3310f4
Create Date: 2026-07-12 02:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a4c9d20b11"
down_revision: Union[str, None] = "c58d7e3310f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "volume_price_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trade_date", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("change_pct", sa.Float(), nullable=False),
        sa.Column("open_price", sa.Float(), nullable=False),
        sa.Column("high_price", sa.Float(), nullable=False),
        sa.Column("low_price", sa.Float(), nullable=False),
        sa.Column("prev_close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("estimated_full_day_amount", sa.Float(), nullable=False),
        sa.Column("turnover", sa.Float(), nullable=False),
        sa.Column("volume_ratio", sa.Float(), nullable=False),
        sa.Column("vwap", sa.Float(), nullable=False),
        sa.Column("price_vs_vwap", sa.Float(), nullable=False),
        sa.Column("high_drawdown", sa.Float(), nullable=False),
        sa.Column("active_buy_amount", sa.Float(), nullable=False),
        sa.Column("active_sell_amount", sa.Float(), nullable=False),
        sa.Column("attack_efficiency", sa.Float(), nullable=False),
        sa.Column("volume_acceleration", sa.Float(), nullable=False),
        sa.Column("pattern", sa.String(length=64), nullable=False),
        sa.Column("data_quality", sa.String(length=32), nullable=False),
        sa.Column("data_source", sa.String(length=64), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("counter_evidence_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_volume_price_snapshots_id"), "volume_price_snapshots", ["id"], unique=False)
    op.create_index(op.f("ix_volume_price_snapshots_trade_date"), "volume_price_snapshots", ["trade_date"], unique=False)
    op.create_index(op.f("ix_volume_price_snapshots_code"), "volume_price_snapshots", ["code"], unique=False)
    op.create_index(op.f("ix_volume_price_snapshots_stage"), "volume_price_snapshots", ["stage"], unique=False)
    op.create_index(op.f("ix_volume_price_snapshots_captured_at"), "volume_price_snapshots", ["captured_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_volume_price_snapshots_captured_at"), table_name="volume_price_snapshots")
    op.drop_index(op.f("ix_volume_price_snapshots_stage"), table_name="volume_price_snapshots")
    op.drop_index(op.f("ix_volume_price_snapshots_code"), table_name="volume_price_snapshots")
    op.drop_index(op.f("ix_volume_price_snapshots_trade_date"), table_name="volume_price_snapshots")
    op.drop_index(op.f("ix_volume_price_snapshots_id"), table_name="volume_price_snapshots")
    op.drop_table("volume_price_snapshots")
