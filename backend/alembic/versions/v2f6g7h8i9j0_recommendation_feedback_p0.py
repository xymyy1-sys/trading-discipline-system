"""bind recommendation feedback to immutable revisions

Revision ID: v2f6g7h8i9j0
Revises: u1e5f6a7b8c9
"""

from __future__ import annotations

import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision = "v2f6g7h8i9j0"
down_revision = "u1e5f6a7b8c9"
branch_labels = None
depends_on = None


def _json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _legacy_decision_hash(row: dict) -> str:
    payload = {
        "level": str(row.get("level") or "INFO"),
        "state": str(row.get("state") or ""),
        "action": str(row.get("action") or ""),
        "recommended_ratio": round(float(row.get("recommended_ratio") or 0), 4),
        "trigger_events": [],
        "invalid_conditions": sorted(_json_list(row.get("invalid_conditions_json"))),
        "recovery_conditions": sorted(_json_list(row.get("recovery_conditions_json"))),
        "rule_version": "legacy-v1",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def upgrade() -> None:
    op.add_column("action_recommendations", sa.Column("target_key", sa.String(64), nullable=False, server_default=""))
    op.add_column("action_recommendations", sa.Column("updated_at", sa.DateTime(), nullable=True))
    op.add_column("action_recommendations", sa.Column("current_revision_id", sa.Integer(), nullable=True))
    op.add_column("action_recommendations", sa.Column("current_decision_hash", sa.String(64), nullable=False, server_default=""))

    op.add_column("action_recommendation_revisions", sa.Column("previous_revision_id", sa.Integer(), nullable=True))
    op.add_column("action_recommendation_revisions", sa.Column("decision_hash", sa.String(64), nullable=False, server_default=""))
    op.add_column("action_recommendation_revisions", sa.Column("trigger_events_json", sa.Text(), nullable=False, server_default="[]"))
    op.add_column("action_recommendation_revisions", sa.Column("decision_context_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("action_recommendation_revisions", sa.Column("rule_version", sa.String(32), nullable=False, server_default="legacy-v1"))
    op.add_column("action_recommendation_revisions", sa.Column("effective_until", sa.DateTime(), nullable=True))

    op.add_column("recommendation_feedback", sa.Column("recommendation_revision_id", sa.Integer(), nullable=True))
    op.add_column("recommendation_feedback", sa.Column("status_code", sa.String(32), nullable=False, server_default=""))
    op.add_column("recommendation_feedback", sa.Column("client_event_id", sa.String(64), nullable=True))
    op.add_column("recommendation_feedback", sa.Column("executed_quantity", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("recommendation_feedback", sa.Column("executed_ratio", sa.Float(), nullable=False, server_default="0"))
    op.add_column("recommendation_feedback", sa.Column("executed_price", sa.Float(), nullable=False, server_default="0"))
    op.add_column("recommendation_feedback", sa.Column("updated_at", sa.DateTime(), nullable=True))

    bind = op.get_bind()

    # Preserve every legacy row.  The newest row in a duplicate target/day
    # group becomes the canonical upsert target; older duplicates retain a
    # unique legacy key so their revisions/outcomes remain auditable.
    recommendations = bind.execute(sa.text(
        "SELECT id, trade_date, holding_id, code, created_at "
        "FROM action_recommendations ORDER BY created_at DESC, id DESC"
    )).mappings().all()
    seen_targets: set[tuple[str, str]] = set()
    for row in recommendations:
        target = f"holding:{row['holding_id']}" if row["holding_id"] is not None else f"code:{row['code']}"
        key = (str(row["trade_date"]), target)
        stored_target = target if key not in seen_targets else f"legacy:{row['id']}"
        seen_targets.add(key)
        bind.execute(
            sa.text(
                "UPDATE action_recommendations "
                "SET target_key=:target_key, updated_at=created_at WHERE id=:id"
            ),
            {"target_key": stored_target, "id": row["id"]},
        )

    revisions = bind.execute(sa.text(
        "SELECT id, recommendation_id, level, state, action, recommended_ratio, "
        "invalid_conditions_json, recovery_conditions_json, created_at "
        "FROM action_recommendation_revisions "
        "ORDER BY recommendation_id, created_at, id"
    )).mappings().all()
    grouped: dict[int, list[dict]] = {}
    for raw_row in revisions:
        grouped.setdefault(int(raw_row["recommendation_id"]), []).append(dict(raw_row))
    for recommendation_id, rows in grouped.items():
        previous_id = None
        latest_id = None
        latest_hash = ""
        for version, row in enumerate(rows, start=1):
            decision_hash = _legacy_decision_hash(row)
            bind.execute(
                sa.text(
                    "UPDATE action_recommendation_revisions SET version=:version, "
                    "previous_revision_id=:previous_id, decision_hash=:decision_hash "
                    "WHERE id=:id"
                ),
                {
                    "version": version,
                    "previous_id": previous_id,
                    "decision_hash": decision_hash,
                    "id": row["id"],
                },
            )
            if previous_id is not None:
                bind.execute(
                    sa.text(
                        "UPDATE action_recommendation_revisions SET effective_until=:effective_until "
                        "WHERE id=:id"
                    ),
                    {"effective_until": row["created_at"], "id": previous_id},
                )
            previous_id = int(row["id"])
            latest_id = int(row["id"])
            latest_hash = decision_hash
        bind.execute(
            sa.text(
                "UPDATE action_recommendations SET current_revision_id=:revision_id, "
                "current_decision_hash=:decision_hash WHERE id=:id"
            ),
            {"revision_id": latest_id, "decision_hash": latest_hash, "id": recommendation_id},
        )

    # Preserve pre-existing base outcomes for audit but remove them from
    # effectiveness samples once immutable revision outcomes exist.
    bind.execute(sa.text(
        "UPDATE recommendation_outcomes "
        "SET status='invalid', data_quality='superseded', "
        "invalid_reason='已由不可变建议版本替代；保留本行仅用于审计。' "
        "WHERE recommendation_revision_id IS NULL AND recommendation_id IN ("
        "SELECT DISTINCT recommendation_id FROM action_recommendation_revisions"
        ")"
    ))

    status_codes = {
        "已执行": "executed",
        "部分执行": "partially_executed",
        "不同意": "rejected",
        "忽略": "rejected",
        "暂不执行": "rejected",
        "未成交": "not_filled",
        "没看到": "not_seen",
        "纪律违背": "discipline_breach",
    }
    feedback_rows = bind.execute(sa.text(
        "SELECT id, recommendation_id, status, created_at FROM recommendation_feedback ORDER BY id"
    )).mappings().all()
    for row in feedback_rows:
        matched_revision = bind.execute(
            sa.text(
                "SELECT id FROM action_recommendation_revisions "
                "WHERE recommendation_id=:recommendation_id AND created_at<=:created_at "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {"recommendation_id": row["recommendation_id"], "created_at": row["created_at"]},
        ).scalar()
        bind.execute(
            sa.text(
                "UPDATE recommendation_feedback SET recommendation_revision_id=:revision_id, "
                "status_code=:status_code, updated_at=created_at WHERE id=:id"
            ),
            {
                "revision_id": matched_revision,
                "status_code": status_codes.get(str(row["status"]), "legacy_unknown"),
                "id": row["id"],
            },
        )

    op.create_index("ix_action_recommendations_target_key", "action_recommendations", ["target_key"])
    op.create_index("ix_action_recommendations_updated_at", "action_recommendations", ["updated_at"])
    op.create_index("ix_action_recommendations_current_revision_id", "action_recommendations", ["current_revision_id"])
    op.create_index("ix_action_recommendations_current_decision_hash", "action_recommendations", ["current_decision_hash"])
    op.create_index(
        "uq_action_recommendations_trade_target",
        "action_recommendations",
        ["trade_date", "target_key"],
        unique=True,
    )
    op.create_index("ix_action_recommendation_revisions_previous_revision_id", "action_recommendation_revisions", ["previous_revision_id"])
    op.create_index("ix_action_recommendation_revisions_decision_hash", "action_recommendation_revisions", ["decision_hash"])
    op.create_index("ix_action_recommendation_revisions_effective_until", "action_recommendation_revisions", ["effective_until"])
    op.create_index(
        "uq_action_recommendation_revision_version",
        "action_recommendation_revisions",
        ["recommendation_id", "version"],
        unique=True,
    )
    op.create_index("ix_recommendation_feedback_recommendation_revision_id", "recommendation_feedback", ["recommendation_revision_id"])
    op.create_index("ix_recommendation_feedback_status_code", "recommendation_feedback", ["status_code"])
    op.create_index("ix_recommendation_feedback_updated_at", "recommendation_feedback", ["updated_at"])
    op.create_index("ix_recommendation_feedback_client_event_id", "recommendation_feedback", ["client_event_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_recommendation_feedback_client_event_id", table_name="recommendation_feedback")
    op.drop_index("ix_recommendation_feedback_updated_at", table_name="recommendation_feedback")
    op.drop_index("ix_recommendation_feedback_status_code", table_name="recommendation_feedback")
    op.drop_index("ix_recommendation_feedback_recommendation_revision_id", table_name="recommendation_feedback")
    op.drop_index("uq_action_recommendation_revision_version", table_name="action_recommendation_revisions")
    op.drop_index("ix_action_recommendation_revisions_effective_until", table_name="action_recommendation_revisions")
    op.drop_index("ix_action_recommendation_revisions_decision_hash", table_name="action_recommendation_revisions")
    op.drop_index("ix_action_recommendation_revisions_previous_revision_id", table_name="action_recommendation_revisions")
    op.drop_index("uq_action_recommendations_trade_target", table_name="action_recommendations")
    op.drop_index("ix_action_recommendations_current_decision_hash", table_name="action_recommendations")
    op.drop_index("ix_action_recommendations_current_revision_id", table_name="action_recommendations")
    op.drop_index("ix_action_recommendations_updated_at", table_name="action_recommendations")
    op.drop_index("ix_action_recommendations_target_key", table_name="action_recommendations")
    for column in (
        "updated_at",
        "executed_price",
        "executed_ratio",
        "executed_quantity",
        "client_event_id",
        "status_code",
        "recommendation_revision_id",
    ):
        op.drop_column("recommendation_feedback", column)
    for column in (
        "effective_until",
        "rule_version",
        "decision_context_json",
        "trigger_events_json",
        "decision_hash",
        "previous_revision_id",
    ):
        op.drop_column("action_recommendation_revisions", column)
    for column in ("current_decision_hash", "current_revision_id", "updated_at", "target_key"):
        op.drop_column("action_recommendations", column)
