"""add turnover provenance

Revision ID: j6f3b8c1e0a2
Revises: i5e2a7b0d9f1
"""
from alembic import op
import sqlalchemy as sa

revision = "j6f3b8c1e0a2"
down_revision = "i5e2a7b0d9f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("volume_price_snapshots", sa.Column("turnover_source", sa.String(length=48), nullable=False, server_default="unavailable"))
    op.add_column("volume_price_snapshots", sa.Column("turnover_reliable", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("volume_price_snapshots", sa.Column("float_cap", sa.Float(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("volume_price_snapshots", "float_cap")
    op.drop_column("volume_price_snapshots", "turnover_reliable")
    op.drop_column("volume_price_snapshots", "turnover_source")
