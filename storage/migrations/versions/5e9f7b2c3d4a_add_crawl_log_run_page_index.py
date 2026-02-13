"""add_crawl_log_run_page_index

Revision ID: 5e9f7b2c3d4a
Revises: 2f1ae345c29f
Create Date: 2026-02-13 10:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5e9f7b2c3d4a"
down_revision: str | Sequence[str] | None = "2f1ae345c29f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema - add composite index on crawl_log for UPDATE queries."""
    op.create_index(
        "idx_crawl_log_run_page",
        "crawl_log",
        ["crawl_run_id", "page_url"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema - remove crawl_log index."""
    op.drop_index("idx_crawl_log_run_page", table_name="crawl_log")
