"""time stop rules

Revision ID: e3a1c5d9b2f4
Revises: d2f6a0b8c9e1
Create Date: 2026-07-12 13:40:00.000000

"""
from typing import Sequence, Union
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision: str = "e3a1c5d9b2f4"
down_revision: Union[str, None] = "d2f6a0b8c9e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    now = datetime.now()
    op.create_table(
        "time_stop_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("script_type", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("confirmation_deadline", sa.String(length=8), nullable=False),
        sa.Column("below_vwap_minutes", sa.Integer(), nullable=False),
        sa.Column("below_vwap_min_bars", sa.Integer(), nullable=False),
        sa.Column("recent_window_minutes", sa.Integer(), nullable=False),
        sa.Column("failed_limit_reseal_pct", sa.Float(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_time_stop_rules_id"), "time_stop_rules", ["id"], unique=False)
    op.create_index(op.f("ix_time_stop_rules_script_type"), "time_stop_rules", ["script_type"], unique=True)
    op.bulk_insert(
        sa.table(
            "time_stop_rules",
            sa.column("script_type", sa.String),
            sa.column("display_name", sa.String),
            sa.column("confirmation_deadline", sa.String),
            sa.column("below_vwap_minutes", sa.Integer),
            sa.column("below_vwap_min_bars", sa.Integer),
            sa.column("recent_window_minutes", sa.Integer),
            sa.column("failed_limit_reseal_pct", sa.Float),
            sa.column("enabled", sa.Boolean),
            sa.column("updated_at", sa.DateTime),
        ),
        [
            {
                "script_type": "default",
                "display_name": "默认剧本",
                "confirmation_deadline": "10:00",
                "below_vwap_minutes": 5,
                "below_vwap_min_bars": 5,
                "recent_window_minutes": 15,
                "failed_limit_reseal_pct": 0.985,
                "enabled": True,
                "updated_at": now,
            },
            {
                "script_type": "breakout",
                "display_name": "打板/冲板",
                "confirmation_deadline": "09:45",
                "below_vwap_minutes": 3,
                "below_vwap_min_bars": 3,
                "recent_window_minutes": 10,
                "failed_limit_reseal_pct": 0.99,
                "enabled": True,
                "updated_at": now,
            },
            {
                "script_type": "trend",
                "display_name": "趋势/容量",
                "confirmation_deadline": "10:30",
                "below_vwap_minutes": 8,
                "below_vwap_min_bars": 6,
                "recent_window_minutes": 20,
                "failed_limit_reseal_pct": 0.985,
                "enabled": True,
                "updated_at": now,
            },
        ],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_time_stop_rules_script_type"), table_name="time_stop_rules")
    op.drop_index(op.f("ix_time_stop_rules_id"), table_name="time_stop_rules")
    op.drop_table("time_stop_rules")
