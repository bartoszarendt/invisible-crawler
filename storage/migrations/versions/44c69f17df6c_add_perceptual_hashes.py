"""add_perceptual_hashes

Revision ID: 44c69f17df6c
Revises: 3b9c2d1f4e8a
Create Date: 2026-02-05 14:16:40.275768

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "44c69f17df6c"
down_revision: str | Sequence[str] | None = "3b9c2d1f4e8a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema - add perceptual hash columns."""
    # Add perceptual hash columns to images table
    op.add_column("images", sa.Column("phash_hash", sa.String(16), nullable=True))
    op.add_column("images", sa.Column("dhash_hash", sa.String(16), nullable=True))

    # Create index on phash for similarity search
    op.create_index("idx_images_phash", "images", ["phash_hash"])

    # Create index on dhash for additional similarity search
    op.create_index("idx_images_dhash", "images", ["dhash_hash"])


def downgrade() -> None:
    """Downgrade schema - remove perceptual hash columns."""
    # Drop indexes
    op.drop_index("idx_images_dhash", table_name="images")
    op.drop_index("idx_images_phash", table_name="images")

    # Drop columns
    op.drop_column("images", "dhash_hash")
    op.drop_column("images", "phash_hash")
