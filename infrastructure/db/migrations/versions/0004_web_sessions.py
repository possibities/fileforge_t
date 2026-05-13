"""web admin session table (phase 2)

Revision ID: 0004_web_sessions
Revises: 0003_web_admin_accounts
Create Date: 2026-05-12

阶段 2:新增 Web 登录 session 表。cookie 保存明文随机 token,数据库只保存
sha256(token) 与 sha256(csrf_token)。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0004_web_sessions"
down_revision: Union[str, None] = "0003_web_admin_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "web_sessions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("app_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("token_hash", name="uq_web_sessions_token_hash"),
    )
    op.create_index("ix_web_sessions_user", "web_sessions", ["user_id"])
    op.create_index("ix_web_sessions_expires", "web_sessions", ["expires_at"])
    op.create_index("ix_web_sessions_revoked", "web_sessions", ["revoked_at"])


def downgrade() -> None:
    raise NotImplementedError("阶段 2 不支持 downgrade,请清库重建")
