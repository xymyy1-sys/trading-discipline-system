"""simulation shadow experiment decisions

Revision ID: s9c3d4e5f6a7
Revises: r8b2c3d4e5f6
"""

from alembic import op
import sqlalchemy as sa


revision = "s9c3d4e5f6a7"
down_revision = "r8b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "simulation_accounts",
        sa.Column("account_type", sa.String(24), nullable=False, server_default="manual"),
    )
    op.add_column(
        "simulation_accounts",
        sa.Column("automation_key", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_simulation_accounts_account_type",
        "simulation_accounts",
        ["account_type"],
    )
    op.create_index(
        "ix_simulation_accounts_automation_key",
        "simulation_accounts",
        ["automation_key"],
        unique=True,
    )
    op.create_table(
        "simulation_shadow_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("signal_key", sa.String(160), nullable=False),
        sa.Column("strategy_source", sa.String(32), nullable=False),
        sa.Column("source_kind", sa.String(48), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("rule_version", sa.String(32), nullable=False, server_default="shadow-v1"),
        sa.Column("source_version", sa.String(80), nullable=False, server_default=""),
        sa.Column("trade_date", sa.String(16), nullable=False),
        sa.Column("source_at", sa.DateTime(), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(64), nullable=False, server_default=""),
        sa.Column("intent", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False, server_default=""),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.UniqueConstraint("account_id", "signal_key", name="uq_sim_shadow_account_signal"),
    )
    for column in (
        "account_id", "signal_key", "strategy_source", "source_kind", "source_id", "rule_version",
        "trade_date", "source_at", "evaluated_at", "code", "intent", "status", "order_id",
    ):
        op.create_index(
            f"ix_simulation_shadow_decisions_{column}",
            "simulation_shadow_decisions",
            [column],
        )


def downgrade() -> None:
    op.drop_table("simulation_shadow_decisions")
    op.drop_index("ix_simulation_accounts_automation_key", table_name="simulation_accounts")
    op.drop_index("ix_simulation_accounts_account_type", table_name="simulation_accounts")
    op.drop_column("simulation_accounts", "automation_key")
    op.drop_column("simulation_accounts", "account_type")
