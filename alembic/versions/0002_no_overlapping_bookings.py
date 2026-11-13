"""no overlapping bookings: EXCLUDE constraint (PostgreSQL)

Запрещает на уровне БД пересечение броней одного номера, пока они
активны (pending / paid / checked_in). Период считается как [заезд, выезд),
поэтому день выезда одного гостя может быть днём заезда другого.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-11
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # на SQLite (локальная разработка) защита остаётся на уровне приложения
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute(
        """
        ALTER TABLE bookings
        ADD CONSTRAINT bookings_no_overlap
        EXCLUDE USING gist (
            room_id WITH =,
            daterange(check_in, check_out, '[)') WITH &&
        )
        WHERE (status IN ('pending', 'paid', 'checked_in'))
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE bookings DROP CONSTRAINT IF EXISTS bookings_no_overlap")
