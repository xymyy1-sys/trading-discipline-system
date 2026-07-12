"""watchlist daily snapshot

Revision ID: m2c7e4f1a8b9
Revises: l8b5d0e3a2c4
"""
from alembic import op
import sqlalchemy as sa

revision = "m2c7e4f1a8b9"
down_revision = "l8b5d0e3a2c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("watchlist_entries") as batch:
        batch.add_column(sa.Column("snapshot_date", sa.String(length=16), nullable=False, server_default=""))
        batch.add_column(sa.Column("category", sa.String(length=32), nullable=False, server_default=""))
        batch.add_column(sa.Column("snapshot_rank", sa.Integer(), nullable=False, server_default="0"))
        batch.create_index("ix_watchlist_entries_snapshot_date", ["snapshot_date"])


def downgrade() -> None:
    with op.batch_alter_table("watchlist_entries") as batch:
        batch.drop_index("ix_watchlist_entries_snapshot_date")
        batch.drop_column("snapshot_rank")
        batch.drop_column("category")
        batch.drop_column("snapshot_date")
