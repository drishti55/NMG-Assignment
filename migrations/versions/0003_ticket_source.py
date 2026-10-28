"""Record where each ticket came from (inbox file path or "api"), so the console can
label seed data and imported tickets instead of leaving their origin unexplained.

Revision ID: 0003
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tickets ADD COLUMN source text")


def downgrade() -> None:
    op.execute("ALTER TABLE tickets DROP COLUMN IF EXISTS source")
