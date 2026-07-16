"""make unified intraday event states idempotent

Revision ID: t0d4e5f6a7b8
Revises: s9c3d4e5f6a7
"""

from alembic import op
import sqlalchemy as sa


revision = "t0d4e5f6a7b8"
down_revision = "s9c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "intraday_evidence_events",
        sa.Column("state_key", sa.String(length=40), nullable=True),
    )
    op.create_index(
        "ix_intraday_evidence_events_state_key",
        "intraday_evidence_events",
        ["state_key"],
    )
    # A unique index is used instead of ALTER TABLE ADD CONSTRAINT so the
    # production SQLite deployment can apply this migration in place.
    op.create_index(
        "uq_intraday_event_trade_state_key",
        "intraday_evidence_events",
        ["trade_date", "state_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_intraday_event_trade_state_key",
        table_name="intraday_evidence_events",
    )
    op.drop_index(
        "ix_intraday_evidence_events_state_key",
        table_name="intraday_evidence_events",
    )
    op.drop_column("intraday_evidence_events", "state_key")
