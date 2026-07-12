"""ai analysis cache

Revision ID: n4d8f2a6c1e3
Revises: m2c7e4f1a8b9
"""
from alembic import op
import sqlalchemy as sa

revision = "n4d8f2a6c1e3"
down_revision = "m2c7e4f1a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_analysis_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("target", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False, server_default="gpt-5.6-sol"),
        sa.Column("input_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False, server_default="completed"),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ai_analysis_cache_scope", "ai_analysis_cache", ["scope"])
    op.create_index("ix_ai_analysis_cache_target", "ai_analysis_cache", ["target"])
    op.create_index("ix_ai_analysis_cache_created_at", "ai_analysis_cache", ["created_at"])


def downgrade() -> None:
    op.drop_table("ai_analysis_cache")
