"""intraday collection and reliability fields

Revision ID: aa91d7f2c0e4
Revises: f3a4c9d20b11
Create Date: 2026-07-12 08:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "aa91d7f2c0e4"
down_revision: Union[str, None] = "f3a4c9d20b11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("position_execution_states", sa.Column("yesterday_quantity", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("profit_protection_snapshots", sa.Column("maximum_profit_at", sa.DateTime(), nullable=True))
    op.add_column("profit_protection_snapshots", sa.Column("day_max_profit_pct", sa.Float(), nullable=False, server_default="0"))
    op.add_column("profit_protection_snapshots", sa.Column("day_max_profit_at", sa.DateTime(), nullable=True))
    op.add_column("intraday_evidence_events", sa.Column("priority", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("intraday_evidence_events", sa.Column("group_key", sa.String(length=64), nullable=False, server_default=""))
    op.add_column("intraday_evidence_events", sa.Column("first_seen_at", sa.DateTime(), nullable=True))
    op.add_column("intraday_evidence_events", sa.Column("last_seen_at", sa.DateTime(), nullable=True))
    op.add_column("intraday_evidence_events", sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("intraday_evidence_events", sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("volume_price_snapshots", sa.Column("vwap_source", sa.String(length=32), nullable=False, server_default="estimated"))
    op.add_column("volume_price_snapshots", sa.Column("minute_bar_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("volume_price_snapshots", sa.Column("vwap_reliable", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "intraday_collection_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("holding_count", sa.Integer(), nullable=False),
        sa.Column("snapshot_count", sa.Integer(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("notes_json", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_intraday_collection_runs_id"), "intraday_collection_runs", ["id"], unique=False)
    op.create_index(op.f("ix_intraday_collection_runs_started_at"), "intraday_collection_runs", ["started_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_intraday_collection_runs_started_at"), table_name="intraday_collection_runs")
    op.drop_index(op.f("ix_intraday_collection_runs_id"), table_name="intraday_collection_runs")
    op.drop_table("intraday_collection_runs")
    op.drop_column("volume_price_snapshots", "vwap_reliable")
    op.drop_column("volume_price_snapshots", "minute_bar_count")
    op.drop_column("volume_price_snapshots", "vwap_source")
    op.drop_column("intraday_evidence_events", "confirmed")
    op.drop_column("intraday_evidence_events", "occurrence_count")
    op.drop_column("intraday_evidence_events", "last_seen_at")
    op.drop_column("intraday_evidence_events", "first_seen_at")
    op.drop_column("intraday_evidence_events", "group_key")
    op.drop_column("intraday_evidence_events", "priority")
    op.drop_column("profit_protection_snapshots", "day_max_profit_at")
    op.drop_column("profit_protection_snapshots", "day_max_profit_pct")
    op.drop_column("profit_protection_snapshots", "maximum_profit_at")
    op.drop_column("position_execution_states", "yesterday_quantity")
