"""mark completed daily sector-turnover archives

Revision ID: y6i9j0k1l2m3
Revises: x4h8i9j0k1l2
"""

from alembic import op
import sqlalchemy as sa


revision = "y6i9j0k1l2m3"
down_revision = "x4h8i9j0k1l2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sector_crowding_daily_snapshots",
        sa.Column(
            "turnover_complete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("sector_crowding_daily_snapshots", "turnover_complete")
