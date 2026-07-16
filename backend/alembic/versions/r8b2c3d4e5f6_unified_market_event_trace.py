"""unified trace fields for intraday market events

Revision ID: r8b2c3d4e5f6
Revises: q7a1b2c3d4e5
"""

from alembic import op
import sqlalchemy as sa


revision = "r8b2c3d4e5f6"
down_revision = "q7a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "intraday_evidence_events",
        sa.Column("counter_evidence_json", sa.Text(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "intraday_evidence_events",
        sa.Column("source", sa.String(length=128), nullable=False, server_default=""),
    )
    op.add_column(
        "intraday_evidence_events",
        sa.Column("source_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "intraday_evidence_events",
        sa.Column("source_published_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "intraday_evidence_events",
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("intraday_evidence_events", "metadata_json")
    op.drop_column("intraday_evidence_events", "source_published_at")
    op.drop_column("intraday_evidence_events", "source_url")
    op.drop_column("intraday_evidence_events", "source")
    op.drop_column("intraday_evidence_events", "counter_evidence_json")
