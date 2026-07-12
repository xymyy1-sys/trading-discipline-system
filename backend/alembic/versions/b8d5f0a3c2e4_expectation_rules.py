"""add editable expectation rules

Revision ID: b8d5f0a3c2e4
Revises: a7c4e9d2f1b3
"""

from alembic import op
import sqlalchemy as sa

revision = "b8d5f0a3c2e4"
down_revision = "a7c4e9d2f1b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "expectation_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("script_type", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False, server_default="*"),
        sa.Column("base_expectation", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(64), nullable=False, server_default=""),
        sa.Column("expected_open_low", sa.Float(), nullable=False),
        sa.Column("expected_open_high", sa.Float(), nullable=False),
        sa.Column("outperform_threshold", sa.Float(), nullable=False),
        sa.Column("underperform_threshold", sa.Float(), nullable=False),
        sa.Column("severe_underperform_threshold", sa.Float(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("script_type", "stage", "base_expectation", name="uq_expectation_rule_scope"),
    )
    op.create_index("ix_expectation_rules_scope", "expectation_rules", ["script_type", "stage", "base_expectation"])


def downgrade() -> None:
    op.drop_index("ix_expectation_rules_scope", table_name="expectation_rules")
    op.drop_table("expectation_rules")
