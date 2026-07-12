"""add daily volume and chip metrics

Revision ID: h4d1f6a9c8e0
Revises: g3c0e5f8b7d9
"""
from alembic import op
import sqlalchemy as sa

revision = "h4d1f6a9c8e0"
down_revision = "g3c0e5f8b7d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = [
        ("ma5", sa.Float(), "0"), ("ma10", sa.Float(), "0"), ("ma20", sa.Float(), "0"),
        ("return_5d", sa.Float(), "0"), ("return_10d", sa.Float(), "0"),
        ("distance_recent_high_pct", sa.Float(), "0"), ("historical_volume_ratio", sa.Float(), "0"),
        ("chip_profit_ratio", sa.Float(), "0"), ("chip_avg_cost", sa.Float(), "0"),
        ("chip_70_concentration", sa.Float(), "0"), ("chip_90_concentration", sa.Float(), "0"),
    ]
    for name, kind, default in columns:
        op.add_column("volume_price_snapshots", sa.Column(name, kind, nullable=False, server_default=default))
    op.add_column("volume_price_snapshots", sa.Column("chip_metrics_estimated", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    for name in ("chip_metrics_estimated", "chip_90_concentration", "chip_70_concentration", "chip_avg_cost", "chip_profit_ratio", "historical_volume_ratio", "distance_recent_high_pct", "return_10d", "return_5d", "ma20", "ma10", "ma5"):
        op.drop_column("volume_price_snapshots", name)
