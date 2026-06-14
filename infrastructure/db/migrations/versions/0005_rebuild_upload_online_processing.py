"""rebuild schema for upload and online processing

Revision ID: 0005_upload_online_processing
Revises: 0004_web_sessions
Create Date: 2026-06-08

This project has no production data. The migration deliberately drops every
application table and recreates the schema from the current SQLAlchemy metadata.
It is the clean break from the earlier DB design to the upload + online batch
processing model.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from infrastructure.db.models import Base


revision: str = "0005_upload_online_processing"
down_revision: Union[str, None] = "0004_web_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    metadata.reflect(bind=bind)

    for table in reversed(metadata.sorted_tables):
        if table.name == "alembic_version":
            continue
        table.drop(bind=bind, checkfirst=True)

    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    raise NotImplementedError("阶段 3 重建迁移不支持 downgrade,请清库重建")
