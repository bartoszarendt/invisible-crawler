"""add_images_url_index

Revision ID: 3b9c2d1f4e8a
Revises: 2a8b1f0e0c7b
Create Date: 2026-02-05 15:30:00.000000

Note: PostgreSQL automatically creates an index for UNIQUE constraints,
but an explicit index makes the intent clearer and ensures consistent
query plans for URL lookups.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3b9c2d1f4e8a"
down_revision: str | Sequence[str] | None = "2a8b1f0e0c7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add explicit index on images.url for faster lookups."""
    # Note: This index may already exist implicitly due to UNIQUE constraint.
    # Using IF NOT EXISTS to make migration idempotent.
    op.execute("CREATE INDEX IF NOT EXISTS idx_images_url ON images(url)")


def downgrade() -> None:
    """Remove the explicit URL index."""
    op.drop_index("idx_images_url", table_name="images")
