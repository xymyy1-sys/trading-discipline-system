"""add strategy templates

Revision ID: c9e6a1b4d3f5
Revises: b8d5f0a3c2e4
"""
from alembic import op
import sqlalchemy as sa

revision = "c9e6a1b4d3f5"
down_revision = "b8d5f0a3c2e4"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "strategy_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(48), nullable=False, unique=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("category", sa.String(32), nullable=False, server_default="general"),
        sa.Column("market_environment_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("prerequisites_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("premarket_expectation_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("auction_conditions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("volume_price_conditions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("buy_confirmation_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("position_limit", sa.Float(), nullable=False, server_default="0"),
        sa.Column("structure_stop_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("invalid_conditions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("holding_management_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("forbidden_actions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

def downgrade() -> None:
    op.drop_table("strategy_templates")
