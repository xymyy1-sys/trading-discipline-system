"""add active flow provenance

Revision ID: g3c0e5f8b7d9
Revises: f2b9d4e7a6c8
"""
from alembic import op
import sqlalchemy as sa

revision = "g3c0e5f8b7d9"
down_revision = "f2b9d4e7a6c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("volume_price_snapshots", sa.Column("active_flow_source", sa.String(48), nullable=False, server_default="unavailable"))
    op.add_column("volume_price_snapshots", sa.Column("active_flow_estimated", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("volume_price_snapshots", "active_flow_estimated")
    op.drop_column("volume_price_snapshots", "active_flow_source")
