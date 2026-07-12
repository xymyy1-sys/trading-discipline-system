"""watchlist entries

Revision ID: l8b5d0e3a2c4
Revises: k7a4c9d2f1b3
"""
from alembic import op
import sqlalchemy as sa

revision = "l8b5d0e3a2c4"
down_revision = "k7a4c9d2f1b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "watchlist_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("code", name="uq_watchlist_entries_code"),
    )
    op.create_index("ix_watchlist_entries_code", "watchlist_entries", ["code"])
    op.create_index("ix_watchlist_entries_status", "watchlist_entries", ["status"])


def downgrade() -> None:
    op.drop_index("ix_watchlist_entries_status", table_name="watchlist_entries")
    op.drop_index("ix_watchlist_entries_code", table_name="watchlist_entries")
    op.drop_table("watchlist_entries")
