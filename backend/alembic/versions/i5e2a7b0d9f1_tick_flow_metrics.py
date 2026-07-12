"""add tick flow metrics

Revision ID: i5e2a7b0d9f1
Revises: h4d1f6a9c8e0
"""
from alembic import op
import sqlalchemy as sa

revision = "i5e2a7b0d9f1"
down_revision = "h4d1f6a9c8e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("volume_price_snapshots", sa.Column("large_order_net_amount", sa.Float(), nullable=False, server_default="0"))
    op.add_column("volume_price_snapshots", sa.Column("large_order_threshold", sa.Float(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("volume_price_snapshots", "large_order_threshold")
    op.drop_column("volume_price_snapshots", "large_order_net_amount")
