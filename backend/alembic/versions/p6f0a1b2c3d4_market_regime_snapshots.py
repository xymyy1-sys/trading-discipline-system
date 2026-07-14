"""full-market regime snapshots

Revision ID: p6f0a1b2c3d4
Revises: o5e9a3b7c2d4
"""

from alembic import op
import sqlalchemy as sa


revision = "p6f0a1b2c3d4"
down_revision = "o5e9a3b7c2d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_regime_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trade_date", sa.String(16), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(255), nullable=False, server_default=""),
        sa.Column("data_quality", sa.String(24), nullable=False, server_default="missing"),
        sa.Column("coverage_ratio", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("active_stock_count", sa.Integer(), nullable=True),
        sa.Column("up_count", sa.Integer(), nullable=True),
        sa.Column("down_count", sa.Integer(), nullable=True),
        sa.Column("flat_count", sa.Integer(), nullable=True),
        sa.Column("up_5pct_count", sa.Integer(), nullable=True),
        sa.Column("down_5pct_count", sa.Integer(), nullable=True),
        sa.Column("limit_up_count", sa.Integer(), nullable=True),
        sa.Column("limit_down_count", sa.Integer(), nullable=True),
        sa.Column("median_change_pct", sa.Float(), nullable=True),
        sa.Column("advance_ratio", sa.Float(), nullable=True),
        sa.Column("turnover_yi", sa.Float(), nullable=True),
        sa.Column("projected_turnover_yi", sa.Float(), nullable=True),
        sa.Column("previous_turnover_yi", sa.Float(), nullable=True),
        sa.Column("avg5_turnover_yi", sa.Float(), nullable=True),
        sa.Column("volume_ratio_previous", sa.Float(), nullable=True),
        sa.Column("volume_ratio_5d", sa.Float(), nullable=True),
        sa.Column("market_main_net_inflow_yi", sa.Float(), nullable=True),
        sa.Column("index_composite_change_pct", sa.Float(), nullable=True),
        sa.Column("index_above_vwap_count", sa.Integer(), nullable=True),
        sa.Column("index_valid_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("indices_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("positive_sector_count", sa.Integer(), nullable=True),
        sa.Column("negative_sector_count", sa.Integer(), nullable=True),
        sa.Column("positive_sector_ratio", sa.Float(), nullable=True),
        sa.Column("sector_above_vwap_ratio", sa.Float(), nullable=True),
        sa.Column("top3_inflow_share", sa.Float(), nullable=True),
        sa.Column("strongest_sectors_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("weakest_sectors_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("regime_code", sa.String(48), nullable=False, server_default="UNKNOWN"),
        sa.Column("regime_name", sa.String(48), nullable=False, server_default="数据不足"),
        sa.Column("risk_level", sa.String(16), nullable=False, server_default="未知"),
        sa.Column("opportunity_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("loss_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("liquidity_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("allowed_actions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("forbidden_actions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("missing_fields_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("notes_json", sa.Text(), nullable=False, server_default="[]"),
    )
    for column in ("trade_date", "captured_at", "data_quality", "regime_code"):
        op.create_index(
            f"ix_market_regime_snapshots_{column}",
            "market_regime_snapshots",
            [column],
        )


def downgrade() -> None:
    op.drop_table("market_regime_snapshots")
