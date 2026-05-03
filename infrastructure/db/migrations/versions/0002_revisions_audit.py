"""metadata_revisions + audit_logs (phase 1B)

Revision ID: 0002_revisions_audit
Revises: 0001_init_phase1
Create Date: 2026-05-03

阶段 1B:把人工修正历史与系统审计日志的两张表落到数据库。
两张表都不带状态机,纯 append-only;不实现 downgrade。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0002_revisions_audit"
down_revision: Union[str, None] = "0001_init_phase1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _jsonb() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "metadata_revisions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "archive_id",
            sa.Integer,
            sa.ForeignKey("archive_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision_no", sa.Integer, nullable=False),
        sa.Column("field_key", sa.String(64), nullable=False),
        sa.Column("field_column", sa.String(64), nullable=True),
        sa.Column("old_value", _jsonb(), nullable=True),
        sa.Column("new_value", _jsonb(), nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("created_by", sa.Integer, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "archive_id", "revision_no", "field_key", name="uq_revision_field"
        ),
    )
    op.create_index(
        "ix_revisions_archive_revision",
        "metadata_revisions",
        ["archive_id", "revision_no"],
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("actor_user_id", sa.Integer, nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=True),
        sa.Column("target_id", sa.Integer, nullable=True),
        sa.Column("before_data", _jsonb(), nullable=True),
        sa.Column("after_data", _jsonb(), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_audit_action_created", "audit_logs", ["action", "created_at"]
    )
    op.create_index(
        "ix_audit_target", "audit_logs", ["target_type", "target_id"]
    )


def downgrade() -> None:
    raise NotImplementedError("阶段 1B 不支持 downgrade,请清库重建")
