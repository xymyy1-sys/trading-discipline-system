"""add account daily risk baseline

Revision ID: f2b9d4e7a6c8
Revises: e1a8c3d6f5b7
"""
from alembic import op
import sqlalchemy as sa

revision = "f2b9d4e7a6c8"
down_revision = "e1a8c3d6f5b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_daily_risk",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trade_date", sa.String(16), nullable=False, unique=True),
        sa.Column("opening_asset", sa.Float(), nullable=False),
        sa.Column("current_asset", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_account_daily_risk_trade_date", "account_daily_risk", ["trade_date"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_account_daily_risk_trade_date", table_name="account_daily_risk")
    op.drop_table("account_daily_risk")
