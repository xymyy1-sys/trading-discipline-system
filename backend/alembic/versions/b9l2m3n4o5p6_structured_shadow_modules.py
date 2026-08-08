"""add structured source modules to shadow decisions

Revision ID: b9l2m3n4o5p6
Revises: a8k1l2m3n4o5
"""

from alembic import op
import sqlalchemy as sa


revision = "b9l2m3n4o5p6"
down_revision = "a8k1l2m3n4o5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "simulation_shadow_decisions",
        sa.Column("source_modules_json", sa.Text(), nullable=False, server_default="[]"),
    )
    op.execute("""
        UPDATE simulation_shadow_decisions
        SET source_modules_json = CASE source_kind
            WHEN 'limit_up_plan_confirmation' THEN '[\"打板预案\"]'
            WHEN 'autonomous_universe_selection' THEN '[\"全市场自主选股\"]'
            WHEN 'autonomous_exploration_sample' THEN '[\"全市场探索样本\"]'
            WHEN 'expectation_volume_pair' THEN '[\"预期×量价\"]'
            WHEN 'pullback_reclaim_confirmation' THEN '[\"回踩确认\",\"预期×量价\"]'
            WHEN 'position_execution_state' THEN '[\"持仓执行\"]'
            WHEN 'dynamic_profit_protection' THEN '[\"利润保护\"]'
            WHEN 'simulation_hard_stop' THEN '[\"硬止损\"]'
            ELSE '[]'
        END
    """)


def downgrade() -> None:
    op.drop_column("simulation_shadow_decisions", "source_modules_json")
