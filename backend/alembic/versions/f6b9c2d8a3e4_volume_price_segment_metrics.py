"""volume price segment metrics

Revision ID: f6b9c2d8a3e4
Revises: e3a1c5d9b2f4
Create Date: 2026-07-12 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6b9c2d8a3e4"
down_revision: Union[str, None] = "e3a1c5d9b2f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("volume_price_snapshots", sa.Column("attack_amount", sa.Float(), nullable=True))
    op.add_column("volume_price_snapshots", sa.Column("pullback_amount", sa.Float(), nullable=True))
    op.add_column("volume_price_snapshots", sa.Column("pullback_amount_ratio", sa.Float(), nullable=True))
    op.add_column("volume_price_snapshots", sa.Column("pullback_sell_ratio", sa.Float(), nullable=True))
    op.execute("UPDATE volume_price_snapshots SET attack_amount = 0 WHERE attack_amount IS NULL")
    op.execute("UPDATE volume_price_snapshots SET pullback_amount = 0 WHERE pullback_amount IS NULL")
    op.execute("UPDATE volume_price_snapshots SET pullback_amount_ratio = 0 WHERE pullback_amount_ratio IS NULL")
    op.execute("UPDATE volume_price_snapshots SET pullback_sell_ratio = 0 WHERE pullback_sell_ratio IS NULL")


def downgrade() -> None:
    op.drop_column("volume_price_snapshots", "pullback_sell_ratio")
    op.drop_column("volume_price_snapshots", "pullback_amount_ratio")
    op.drop_column("volume_price_snapshots", "pullback_amount")
    op.drop_column("volume_price_snapshots", "attack_amount")
