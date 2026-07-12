"""add calibration runs

Revision ID: k7a4c9d2f1b3
Revises: j6f3b8c1e0a2
"""
from alembic import op
import sqlalchemy as sa

revision = "k7a4c9d2f1b3"
down_revision = "j6f3b8c1e0a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "calibration_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("metric_key", sa.String(length=48), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="applied"),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("before_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("after_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("rolled_back_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_calibration_runs_metric_key", "calibration_runs", ["metric_key"])
    op.create_index("ix_calibration_runs_status", "calibration_runs", ["status"])
    op.create_index("ix_calibration_runs_created_at", "calibration_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_calibration_runs_created_at", table_name="calibration_runs")
    op.drop_index("ix_calibration_runs_status", table_name="calibration_runs")
    op.drop_index("ix_calibration_runs_metric_key", table_name="calibration_runs")
    op.drop_table("calibration_runs")
