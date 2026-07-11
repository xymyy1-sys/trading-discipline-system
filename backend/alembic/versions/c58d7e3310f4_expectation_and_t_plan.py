"""expectation and t plan

Revision ID: c58d7e3310f4
Revises: 9c1f7a2d4b11
Create Date: 2026-07-11 21:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c58d7e3310f4"
down_revision: Union[str, None] = "9c1f7a2d4b11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "expectation_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trade_date", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("base_expectation", sa.String(length=32), nullable=False),
        sa.Column("expected_open_low", sa.Float(), nullable=False),
        sa.Column("expected_open_high", sa.Float(), nullable=False),
        sa.Column("outperform_threshold", sa.Float(), nullable=False),
        sa.Column("underperform_threshold", sa.Float(), nullable=False),
        sa.Column("severe_underperform_threshold", sa.Float(), nullable=False),
        sa.Column("actual_open_pct", sa.Float(), nullable=False),
        sa.Column("actual_change_pct", sa.Float(), nullable=False),
        sa.Column("expectation_gap_score", sa.Integer(), nullable=False),
        sa.Column("expectation_result", sa.String(length=32), nullable=False),
        sa.Column("state_transition", sa.String(length=48), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("counter_evidence_json", sa.Text(), nullable=False),
        sa.Column("suggestion", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_expectation_snapshots_id"), "expectation_snapshots", ["id"], unique=False)
    op.create_index(op.f("ix_expectation_snapshots_trade_date"), "expectation_snapshots", ["trade_date"], unique=False)
    op.create_index(op.f("ix_expectation_snapshots_code"), "expectation_snapshots", ["code"], unique=False)
    op.create_index(op.f("ix_expectation_snapshots_stage"), "expectation_snapshots", ["stage"], unique=False)
    op.create_index(op.f("ix_expectation_snapshots_created_at"), "expectation_snapshots", ["created_at"], unique=False)

    op.create_table(
        "t_trade_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("holding_id", sa.Integer(), nullable=False),
        sa.Column("trade_date", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("t_type", sa.String(length=24), nullable=False),
        sa.Column("planned_sell_price", sa.Float(), nullable=False),
        sa.Column("planned_sell_quantity", sa.Integer(), nullable=False),
        sa.Column("buyback_price_low", sa.Float(), nullable=False),
        sa.Column("buyback_price_high", sa.Float(), nullable=False),
        sa.Column("buyback_conditions_json", sa.Text(), nullable=False),
        sa.Column("cancel_conditions_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("actual_sell_price", sa.Float(), nullable=False),
        sa.Column("actual_buyback_price", sa.Float(), nullable=False),
        sa.Column("actual_quantity", sa.Integer(), nullable=False),
        sa.Column("cost_reduction", sa.Float(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_t_trade_plans_id"), "t_trade_plans", ["id"], unique=False)
    op.create_index(op.f("ix_t_trade_plans_holding_id"), "t_trade_plans", ["holding_id"], unique=False)
    op.create_index(op.f("ix_t_trade_plans_trade_date"), "t_trade_plans", ["trade_date"], unique=False)
    op.create_index(op.f("ix_t_trade_plans_code"), "t_trade_plans", ["code"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_t_trade_plans_code"), table_name="t_trade_plans")
    op.drop_index(op.f("ix_t_trade_plans_trade_date"), table_name="t_trade_plans")
    op.drop_index(op.f("ix_t_trade_plans_holding_id"), table_name="t_trade_plans")
    op.drop_index(op.f("ix_t_trade_plans_id"), table_name="t_trade_plans")
    op.drop_table("t_trade_plans")
    op.drop_index(op.f("ix_expectation_snapshots_created_at"), table_name="expectation_snapshots")
    op.drop_index(op.f("ix_expectation_snapshots_stage"), table_name="expectation_snapshots")
    op.drop_index(op.f("ix_expectation_snapshots_code"), table_name="expectation_snapshots")
    op.drop_index(op.f("ix_expectation_snapshots_trade_date"), table_name="expectation_snapshots")
    op.drop_index(op.f("ix_expectation_snapshots_id"), table_name="expectation_snapshots")
    op.drop_table("expectation_snapshots")
