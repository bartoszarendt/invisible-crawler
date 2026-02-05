"""add_crawl_runs

Revision ID: 3b65381b0f4e
Revises: 44c69f17df6c
Create Date: 2026-02-05 14:17:06.864467

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "3b65381b0f4e"
down_revision: str | Sequence[str] | None = "44c69f17df6c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema - add crawl_runs table."""
    # Create crawl_runs table
    op.create_table(
        "crawl_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mode", sa.String(20), nullable=False),  # 'discovery' | 'refresh'
        sa.Column("pages_crawled", sa.Integer(), server_default=sa.text("0")),
        sa.Column("images_found", sa.Integer(), server_default=sa.text("0")),
        sa.Column("images_downloaded", sa.Integer(), server_default=sa.text("0")),
        sa.Column("seed_source", sa.String(255), nullable=True),
        sa.Column(
            "status", sa.String(20), server_default="running"
        ),  # 'running' | 'completed' | 'failed'
        sa.Column("error_message", sa.Text(), nullable=True),
    )

    # Create indexes for crawl_runs
    op.create_index("idx_crawl_runs_started_at", "crawl_runs", ["started_at"])
    op.create_index("idx_crawl_runs_mode", "crawl_runs", ["mode"])
    op.create_index("idx_crawl_runs_status", "crawl_runs", ["status"])

    # Add crawl_run_id to crawl_log table
    op.add_column(
        "crawl_log", sa.Column("crawl_run_id", postgresql.UUID(as_uuid=True), nullable=True)
    )

    # Create foreign key constraint
    op.create_foreign_key(
        "fk_crawl_log_crawl_run",
        "crawl_log",
        "crawl_runs",
        ["crawl_run_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Create index on crawl_run_id
    op.create_index("idx_crawl_log_run_id", "crawl_log", ["crawl_run_id"])


def downgrade() -> None:
    """Downgrade schema - remove crawl_runs table."""
    # Drop foreign key and column from crawl_log
    op.drop_index("idx_crawl_log_run_id", table_name="crawl_log")
    op.drop_constraint("fk_crawl_log_crawl_run", "crawl_log", type_="foreignkey")
    op.drop_column("crawl_log", "crawl_run_id")

    # Drop crawl_runs table
    op.drop_index("idx_crawl_runs_status", table_name="crawl_runs")
    op.drop_index("idx_crawl_runs_mode", table_name="crawl_runs")
    op.drop_index("idx_crawl_runs_started_at", table_name="crawl_runs")
    op.drop_table("crawl_runs")
