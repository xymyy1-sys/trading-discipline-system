"""add hash chained audit log

Revision ID: e1a8c3d6f5b7
Revises: d0f7b2c5e4a6
"""
from alembic import op
import sqlalchemy as sa

revision = "e1a8c3d6f5b7"
down_revision = "d0f7b2c5e4a6"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("method", sa.String(12), nullable=False),
        sa.Column("path", sa.String(255), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("previous_hash", sa.String(64), nullable=False),
        sa.Column("entry_hash", sa.String(64), nullable=False, unique=True),
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

def downgrade() -> None:
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
