"""add versioned simulation rule releases

Revision ID: z7j0k1l2m3n4
Revises: y6i9j0k1l2m3
"""

from alembic import op
import sqlalchemy as sa


revision = "z7j0k1l2m3n4"
down_revision = "y6i9j0k1l2m3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "simulation_rule_releases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("baseline_rule_version", sa.String(64), nullable=False, server_default=""),
        sa.Column("candidate_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="TRAINING"),
        sa.Column("parameters_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("rationale_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("baseline_closed_trade_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("activation_closed_trade_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("forward_control_samples", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("forward_candidate_samples", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("control_metrics_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("candidate_metrics_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("validated_at", sa.DateTime(), nullable=True),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(), nullable=True),
        sa.Column("rollback_reason", sa.Text(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "candidate_hash", name="uq_sim_rule_release_candidate"),
    )
    op.create_index("ix_simulation_rule_releases_account_id", "simulation_rule_releases", ["account_id"])
    op.create_index("ix_simulation_rule_releases_rule_version", "simulation_rule_releases", ["rule_version"])
    op.create_index("ix_simulation_rule_releases_status", "simulation_rule_releases", ["status"])
    op.create_index("ix_simulation_rule_releases_candidate_hash", "simulation_rule_releases", ["candidate_hash"])
    op.create_index("ix_simulation_rule_releases_created_at", "simulation_rule_releases", ["created_at"])


def downgrade() -> None:
    op.drop_table("simulation_rule_releases")
