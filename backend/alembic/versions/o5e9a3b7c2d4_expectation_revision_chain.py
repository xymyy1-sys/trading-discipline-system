"""expectation revision chain and scenario tree

Revision ID: o5e9a3b7c2d4
Revises: n4d8f2a6c1e3
"""

from alembic import op
import sqlalchemy as sa


revision = "o5e9a3b7c2d4"
down_revision = "n4d8f2a6c1e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "action_recommendation_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("recommendation_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("level", sa.String(24), nullable=False, server_default="INFO"),
        sa.Column("state", sa.String(48), nullable=False, server_default=""),
        sa.Column("action", sa.String(64), nullable=False, server_default=""),
        sa.Column("recommended_ratio", sa.Float(), nullable=False, server_default="0"),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("counter_evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("invalid_conditions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("recovery_conditions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_action_recommendation_revisions_recommendation_id", "action_recommendation_revisions", ["recommendation_id"])
    op.create_index("ix_action_recommendation_revisions_created_at", "action_recommendation_revisions", ["created_at"])
    op.add_column("recommendation_feedback", sa.Column("trade_id", sa.Integer(), nullable=True))
    op.add_column("recommendation_feedback", sa.Column("result", sa.String(32), nullable=False, server_default="待匹配成交"))
    op.create_index("ix_recommendation_feedback_trade_id", "recommendation_feedback", ["trade_id"])
    op.add_column("watchlist_entries", sa.Column("entry_reason", sa.Text(), nullable=False, server_default=""))
    op.add_column("watchlist_entries", sa.Column("exit_reason", sa.Text(), nullable=False, server_default=""))
    op.add_column("watchlist_entries", sa.Column("exited_at", sa.DateTime(), nullable=True))
    op.add_column("watchlist_entries", sa.Column("converted_at", sa.DateTime(), nullable=True))
    op.create_table(
        "expectation_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("expectation_snapshot_id", sa.Integer(), nullable=False),
        sa.Column("previous_revision_id", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("trade_date", sa.String(16), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(64), nullable=False, server_default=""),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("trigger", sa.String(48), nullable=False, server_default="collector"),
        sa.Column("base_expectation", sa.String(32), nullable=False, server_default="UNKNOWN"),
        sa.Column("expected_open_low", sa.Float(), nullable=False, server_default="0"),
        sa.Column("expected_open_high", sa.Float(), nullable=False, server_default="0"),
        sa.Column("actual_open_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("actual_change_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("expectation_gap_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expectation_result", sa.String(32), nullable=False, server_default="MATCHED"),
        sa.Column("state_transition", sa.String(48), nullable=False, server_default="MATCHED"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("volume_price_state", sa.String(64), nullable=False, server_default=""),
        sa.Column("vwap", sa.Float(), nullable=False, server_default="0"),
        sa.Column("price_vs_vwap", sa.Float(), nullable=False, server_default="0"),
        sa.Column("data_quality", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("counter_evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("invalid_conditions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("suggestion", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for column in ("expectation_snapshot_id", "previous_revision_id", "trade_date", "code", "stage", "created_at"):
        op.create_index(f"ix_expectation_revisions_{column}", "expectation_revisions", [column])

    op.create_table(
        "expectation_scenarios",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision_id", sa.Integer(), nullable=False),
        sa.Column("scenario_type", sa.String(32), nullable=False),
        sa.Column("probability", sa.Float(), nullable=False, server_default="0"),
        sa.Column("expected_low", sa.Float(), nullable=False, server_default="0"),
        sa.Column("expected_high", sa.Float(), nullable=False, server_default="0"),
        sa.Column("validation_conditions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("invalid_conditions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("action_discipline", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for column in ("revision_id", "scenario_type", "created_at"):
        op.create_index(f"ix_expectation_scenarios_{column}", "expectation_scenarios", [column])


def downgrade() -> None:
    op.drop_table("expectation_scenarios")
    op.drop_table("expectation_revisions")
    op.drop_column("watchlist_entries", "converted_at")
    op.drop_column("watchlist_entries", "exited_at")
    op.drop_column("watchlist_entries", "exit_reason")
    op.drop_column("watchlist_entries", "entry_reason")
    op.drop_table("action_recommendation_revisions")
    op.drop_index("ix_recommendation_feedback_trade_id", table_name="recommendation_feedback")
    op.drop_column("recommendation_feedback", "result")
    op.drop_column("recommendation_feedback", "trade_id")
