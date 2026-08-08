"""add system evolution proposals and staged board promotion samples

Revision ID: a8k1l2m3n4o5
Revises: z7j0k1l2m3n4
"""

from alembic import op
import sqlalchemy as sa


revision = "a8k1l2m3n4o5"
down_revision = "z7j0k1l2m3n4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "limit_up_promotion_samples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("signal_date", sa.String(16), nullable=False),
        sa.Column("evaluation_date", sa.String(16), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(64), nullable=False, server_default=""),
        sa.Column("from_level", sa.Integer(), nullable=False),
        sa.Column("target_level", sa.Integer(), nullable=False),
        sa.Column("theme", sa.String(96), nullable=False, server_default=""),
        sa.Column("roles_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("features_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("model_version", sa.String(32), nullable=False, server_default="promotion-v1"),
        sa.Column("prior_probability", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence_low", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence_high", sa.Float(), nullable=False, server_default="100"),
        sa.Column("historical_sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("actual_level", sa.Integer(), nullable=True),
        sa.Column("outcome_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("signal_date", "code", "from_level", name="uq_limit_up_promotion_signal"),
    )
    for column in ("signal_date", "evaluation_date", "code", "from_level", "target_level", "theme", "model_version", "status", "created_at", "evaluated_at"):
        op.create_index(f"ix_limit_up_promotion_samples_{column}", "limit_up_promotion_samples", [column])

    op.create_table(
        "system_improvement_proposals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proposal_key", sa.String(64), nullable=False),
        sa.Column("proposal_hash", sa.String(64), nullable=False),
        sa.Column("trade_date", sa.String(16), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("level", sa.String(24), nullable=False, server_default="strategy"),
        sa.Column("module_key", sa.String(64), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("problem", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("proposed_change", sa.Text(), nullable=False),
        sa.Column("expected_effect", sa.Text(), nullable=False, server_default=""),
        sa.Column("risks_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("acceptance_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("priority", sa.String(8), nullable=False, server_default="P2"),
        sa.Column("status", sa.String(32), nullable=False, server_default="PROPOSED"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proposal_key"),
        sa.UniqueConstraint("proposal_hash", name="uq_system_improvement_proposal_hash"),
    )
    for column in ("proposal_key", "proposal_hash", "trade_date", "account_id", "level", "module_key", "priority", "status", "created_at"):
        op.create_index(f"ix_system_improvement_proposals_{column}", "system_improvement_proposals", [column])


def downgrade() -> None:
    op.drop_table("system_improvement_proposals")
    op.drop_table("limit_up_promotion_samples")
