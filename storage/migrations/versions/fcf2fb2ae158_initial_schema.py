"""initial_schema

Revision ID: fcf2fb2ae158
Revises:
Create Date: 2026-02-05 11:10:13.352331

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "fcf2fb2ae158"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Enable UUID extension
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # Images table
    op.create_table(
        "images",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("url", sa.Text(), nullable=False, unique=True),
        sa.Column("sha256_hash", sa.String(64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("format", sa.String(10), nullable=True),
        sa.Column("content_type", sa.String(100), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column(
            "discovered_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("download_success", sa.Boolean(), server_default=sa.text("FALSE")),
        sa.Column("invisible_id_detected", sa.Boolean(), nullable=True),
        sa.Column("invisible_id_payload", sa.Text(), nullable=True),
        sa.Column("invisible_id_confidence", sa.Float(), nullable=True),
        sa.Column("invisible_id_version", sa.String(20), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Create indexes for images
    op.create_index("idx_images_sha256", "images", ["sha256_hash"])
    op.create_index("idx_images_discovered_at", "images", ["discovered_at"])
    op.create_index("idx_images_download_success", "images", ["download_success"])

    # Crawl log table
    op.create_table(
        "crawl_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("page_url", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column(
            "crawled_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column("images_found", sa.Integer(), server_default=sa.text("0")),
        sa.Column("images_downloaded", sa.Integer(), server_default=sa.text("0")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("crawl_type", sa.String(20), server_default="discovery"),
    )

    # Create indexes for crawl_log
    op.create_index("idx_crawl_log_domain", "crawl_log", ["domain"])
    op.create_index("idx_crawl_log_crawled_at", "crawl_log", ["crawled_at"])
    op.create_index("idx_crawl_log_status", "crawl_log", ["status"])

    # Provenance table
    op.create_table(
        "provenance",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("image_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_page_url", sa.Text(), nullable=False),
        sa.Column("source_domain", sa.String(255), nullable=False),
        sa.Column(
            "discovered_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("discovery_type", sa.String(20), server_default="discovery"),
        sa.ForeignKeyConstraint(["image_id"], ["images.id"], ondelete="CASCADE"),
    )

    # Create indexes for provenance
    op.create_index("idx_provenance_image_id", "provenance", ["image_id"])
    op.create_index("idx_provenance_source_domain", "provenance", ["source_domain"])


def downgrade() -> None:
    """Downgrade schema."""
    # Drop tables in reverse order (respecting foreign keys)
    op.drop_index("idx_provenance_source_domain", table_name="provenance")
    op.drop_index("idx_provenance_image_id", table_name="provenance")
    op.drop_table("provenance")

    op.drop_index("idx_crawl_log_status", table_name="crawl_log")
    op.drop_index("idx_crawl_log_crawled_at", table_name="crawl_log")
    op.drop_index("idx_crawl_log_domain", table_name="crawl_log")
    op.drop_table("crawl_log")

    op.drop_index("idx_images_download_success", table_name="images")
    op.drop_index("idx_images_discovered_at", table_name="images")
    op.drop_index("idx_images_sha256", table_name="images")
    op.drop_table("images")
