"""position state history

Revision ID: b7d4e6c8f901
Revises: aa91d7f2c0e4
Create Date: 2026-07-12 12:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7d4e6c8f901"
down_revision: Union[str, None] = "aa91d7f2c0e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "position_state_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("holding_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("trade_date", sa.String(length=16), nullable=False),
        sa.Column("old_state", sa.String(length=48), nullable=False),
        sa.Column("new_state", sa.String(length=48), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_position_state_history_id"), "position_state_history", ["id"], unique=False)
    op.create_index(op.f("ix_position_state_history_holding_id"), "position_state_history", ["holding_id"], unique=False)
    op.create_index(op.f("ix_position_state_history_code"), "position_state_history", ["code"], unique=False)
    op.create_index(op.f("ix_position_state_history_trade_date"), "position_state_history", ["trade_date"], unique=False)
    op.create_index(op.f("ix_position_state_history_new_state"), "position_state_history", ["new_state"], unique=False)
    op.create_index(op.f("ix_position_state_history_captured_at"), "position_state_history", ["captured_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_position_state_history_captured_at"), table_name="position_state_history")
    op.drop_index(op.f("ix_position_state_history_new_state"), table_name="position_state_history")
    op.drop_index(op.f("ix_position_state_history_trade_date"), table_name="position_state_history")
    op.drop_index(op.f("ix_position_state_history_code"), table_name="position_state_history")
    op.drop_index(op.f("ix_position_state_history_holding_id"), table_name="position_state_history")
    op.drop_index(op.f("ix_position_state_history_id"), table_name="position_state_history")
    op.drop_table("position_state_history")
