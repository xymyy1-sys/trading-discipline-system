"""stop source fields

Revision ID: d2f6a0b8c9e1
Revises: b7d4e6c8f901
Create Date: 2026-07-12 13:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d2f6a0b8c9e1"
down_revision: Union[str, None] = "b7d4e6c8f901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "position_execution_states",
        sa.Column("stop_source", sa.String(length=48), nullable=False, server_default="fallback_candidate"),
    )
    op.add_column(
        "position_execution_states",
        sa.Column("stop_source_detail", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("position_execution_states", "stop_source_detail")
    op.drop_column("position_execution_states", "stop_source")
