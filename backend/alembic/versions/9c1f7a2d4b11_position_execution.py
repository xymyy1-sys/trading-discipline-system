"""position execution engine

Revision ID: 9c1f7a2d4b11
Revises: c27db0728ae7
Create Date: 2026-07-11 21:34:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9c1f7a2d4b11"
down_revision: Union[str, None] = "c27db0728ae7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "position_execution_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("holding_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("trade_date", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=48), nullable=False),
        sa.Column("expectation_state", sa.String(length=48), nullable=False),
        sa.Column("volume_price_state", sa.String(length=64), nullable=False),
        sa.Column("sector_state", sa.String(length=64), nullable=False),
        sa.Column("current_quantity", sa.Integer(), nullable=False),
        sa.Column("sellable_quantity", sa.Integer(), nullable=False),
        sa.Column("today_buy_quantity", sa.Integer(), nullable=False),
        sa.Column("current_position_ratio", sa.Float(), nullable=False),
        sa.Column("recommended_position_ratio", sa.Float(), nullable=False),
        sa.Column("recommended_action", sa.String(length=64), nullable=False),
        sa.Column("recommended_reduce_ratio", sa.Float(), nullable=False),
        sa.Column("structure_stop_price", sa.Float(), nullable=False),
        sa.Column("hard_stop_price", sa.Float(), nullable=False),
        sa.Column("trailing_stop_price", sa.Float(), nullable=False),
        sa.Column("profit_protection_price", sa.Float(), nullable=False),
        sa.Column("t_eligible", sa.Boolean(), nullable=False),
        sa.Column("t_type", sa.String(length=24), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("counter_evidence_json", sa.Text(), nullable=False),
        sa.Column("invalid_conditions_json", sa.Text(), nullable=False),
        sa.Column("recovery_conditions_json", sa.Text(), nullable=False),
        sa.Column("data_quality", sa.String(length=32), nullable=False),
        sa.Column("data_time", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_position_execution_states_id"), "position_execution_states", ["id"], unique=False)
    op.create_index(op.f("ix_position_execution_states_holding_id"), "position_execution_states", ["holding_id"], unique=False)
    op.create_index(op.f("ix_position_execution_states_code"), "position_execution_states", ["code"], unique=False)
    op.create_index(op.f("ix_position_execution_states_name"), "position_execution_states", ["name"], unique=False)
    op.create_index(op.f("ix_position_execution_states_trade_date"), "position_execution_states", ["trade_date"], unique=False)

    op.create_table(
        "profit_protection_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("holding_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("current_profit_pct", sa.Float(), nullable=False),
        sa.Column("maximum_profit_pct", sa.Float(), nullable=False),
        sa.Column("profit_drawdown_pct", sa.Float(), nullable=False),
        sa.Column("maximum_price", sa.Float(), nullable=False),
        sa.Column("protection_level", sa.String(length=32), nullable=False),
        sa.Column("protection_floor", sa.Float(), nullable=False),
        sa.Column("triggered", sa.Boolean(), nullable=False),
        sa.Column("recommended_action", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_profit_protection_snapshots_id"), "profit_protection_snapshots", ["id"], unique=False)
    op.create_index(op.f("ix_profit_protection_snapshots_holding_id"), "profit_protection_snapshots", ["holding_id"], unique=False)
    op.create_index(op.f("ix_profit_protection_snapshots_code"), "profit_protection_snapshots", ["code"], unique=False)
    op.create_index(op.f("ix_profit_protection_snapshots_captured_at"), "profit_protection_snapshots", ["captured_at"], unique=False)

    op.create_table(
        "intraday_evidence_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trade_date", sa.String(length=16), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("scope", sa.String(length=24), nullable=False),
        sa.Column("target_code", sa.String(length=16), nullable=False),
        sa.Column("target_name", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("severity", sa.String(length=24), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("previous_value", sa.Float(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("recommendation_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_intraday_evidence_events_id"), "intraday_evidence_events", ["id"], unique=False)
    op.create_index(op.f("ix_intraday_evidence_events_trade_date"), "intraday_evidence_events", ["trade_date"], unique=False)
    op.create_index(op.f("ix_intraday_evidence_events_captured_at"), "intraday_evidence_events", ["captured_at"], unique=False)
    op.create_index(op.f("ix_intraday_evidence_events_target_code"), "intraday_evidence_events", ["target_code"], unique=False)
    op.create_index(op.f("ix_intraday_evidence_events_event_type"), "intraday_evidence_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_intraday_evidence_events_recommendation_id"), "intraday_evidence_events", ["recommendation_id"], unique=False)

    op.create_table(
        "action_recommendations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trade_date", sa.String(length=16), nullable=False),
        sa.Column("holding_id", sa.Integer(), nullable=True),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("level", sa.String(length=24), nullable=False),
        sa.Column("state", sa.String(length=48), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("recommended_ratio", sa.Float(), nullable=False),
        sa.Column("trigger_events_json", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("counter_evidence_json", sa.Text(), nullable=False),
        sa.Column("invalid_conditions_json", sa.Text(), nullable=False),
        sa.Column("recovery_conditions_json", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_action_recommendations_id"), "action_recommendations", ["id"], unique=False)
    op.create_index(op.f("ix_action_recommendations_trade_date"), "action_recommendations", ["trade_date"], unique=False)
    op.create_index(op.f("ix_action_recommendations_holding_id"), "action_recommendations", ["holding_id"], unique=False)
    op.create_index(op.f("ix_action_recommendations_code"), "action_recommendations", ["code"], unique=False)
    op.create_index(op.f("ix_action_recommendations_created_at"), "action_recommendations", ["created_at"], unique=False)

    op.create_table(
        "recommendation_feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recommendation_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_recommendation_feedback_id"), "recommendation_feedback", ["id"], unique=False)
    op.create_index(op.f("ix_recommendation_feedback_recommendation_id"), "recommendation_feedback", ["recommendation_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_recommendation_feedback_recommendation_id"), table_name="recommendation_feedback")
    op.drop_index(op.f("ix_recommendation_feedback_id"), table_name="recommendation_feedback")
    op.drop_table("recommendation_feedback")
    op.drop_index(op.f("ix_action_recommendations_created_at"), table_name="action_recommendations")
    op.drop_index(op.f("ix_action_recommendations_code"), table_name="action_recommendations")
    op.drop_index(op.f("ix_action_recommendations_holding_id"), table_name="action_recommendations")
    op.drop_index(op.f("ix_action_recommendations_trade_date"), table_name="action_recommendations")
    op.drop_index(op.f("ix_action_recommendations_id"), table_name="action_recommendations")
    op.drop_table("action_recommendations")
    op.drop_index(op.f("ix_intraday_evidence_events_recommendation_id"), table_name="intraday_evidence_events")
    op.drop_index(op.f("ix_intraday_evidence_events_event_type"), table_name="intraday_evidence_events")
    op.drop_index(op.f("ix_intraday_evidence_events_target_code"), table_name="intraday_evidence_events")
    op.drop_index(op.f("ix_intraday_evidence_events_captured_at"), table_name="intraday_evidence_events")
    op.drop_index(op.f("ix_intraday_evidence_events_trade_date"), table_name="intraday_evidence_events")
    op.drop_index(op.f("ix_intraday_evidence_events_id"), table_name="intraday_evidence_events")
    op.drop_table("intraday_evidence_events")
    op.drop_index(op.f("ix_profit_protection_snapshots_captured_at"), table_name="profit_protection_snapshots")
    op.drop_index(op.f("ix_profit_protection_snapshots_code"), table_name="profit_protection_snapshots")
    op.drop_index(op.f("ix_profit_protection_snapshots_holding_id"), table_name="profit_protection_snapshots")
    op.drop_index(op.f("ix_profit_protection_snapshots_id"), table_name="profit_protection_snapshots")
    op.drop_table("profit_protection_snapshots")
    op.drop_index(op.f("ix_position_execution_states_trade_date"), table_name="position_execution_states")
    op.drop_index(op.f("ix_position_execution_states_name"), table_name="position_execution_states")
    op.drop_index(op.f("ix_position_execution_states_code"), table_name="position_execution_states")
    op.drop_index(op.f("ix_position_execution_states_holding_id"), table_name="position_execution_states")
    op.drop_index(op.f("ix_position_execution_states_id"), table_name="position_execution_states")
    op.drop_table("position_execution_states")
