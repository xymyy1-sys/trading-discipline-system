"""add T execution feedback quantities

Revision ID: a7c4e9d2f1b3
Revises: f6b9c2d8a3e4
"""

from alembic import op
import sqlalchemy as sa

revision = "a7c4e9d2f1b3"
down_revision = "f6b9c2d8a3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("t_trade_plans", sa.Column("actual_sell_quantity", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("t_trade_plans", sa.Column("actual_buyback_quantity", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("t_trade_plans", sa.Column("execution_note", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("t_trade_plans", "execution_note")
    op.drop_column("t_trade_plans", "actual_buyback_quantity")
    op.drop_column("t_trade_plans", "actual_sell_quantity")
