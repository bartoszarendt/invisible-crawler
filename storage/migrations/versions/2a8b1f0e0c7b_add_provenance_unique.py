"""add_provenance_unique

Revision ID: 2a8b1f0e0c7b
Revises: fcf2fb2ae158
Create Date: 2026-02-05 13:25:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2a8b1f0e0c7b"
down_revision: str | Sequence[str] | None = "fcf2fb2ae158"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_unique_constraint(
        "uq_provenance_image_page",
        "provenance",
        ["image_id", "source_page_url"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_provenance_image_page", "provenance", type_="unique")
