"""add persistent data capture quality snapshots

Revision ID: d0f7b2c5e4a6
Revises: c9e6a1b4d3f5
"""
from alembic import op
import sqlalchemy as sa

revision = "d0f7b2c5e4a6"
down_revision = "c9e6a1b4d3f5"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "data_capture_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trade_date", sa.String(16), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("data_type", sa.String(32), nullable=False),
        sa.Column("target_code", sa.String(32), nullable=False),
        sa.Column("target_name", sa.String(64), nullable=False, server_default=""),
        sa.Column("raw_value_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("normalized_value_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("quality", sa.String(32), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_stale", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_degraded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_estimated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw_payload_hash", sa.String(64), nullable=False),
    )
    op.create_index("ix_data_capture_source_time", "data_capture_snapshots", ["source", "captured_at"])
    op.create_index("ix_data_capture_target_time", "data_capture_snapshots", ["target_code", "captured_at"])

def downgrade() -> None:
    op.drop_index("ix_data_capture_target_time", table_name="data_capture_snapshots")
    op.drop_index("ix_data_capture_source_time", table_name="data_capture_snapshots")
    op.drop_table("data_capture_snapshots")
