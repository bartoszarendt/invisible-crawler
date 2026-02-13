"""add_image_assets_observations

Revision ID: 6f0a8c3d4e5b
Revises: 5e9f7b2c3d4a
Create Date: 2026-02-13 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "6f0a8c3d4e5b"
down_revision: str | Sequence[str] | None = "5e9f7b2c3d4a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema - add image_assets, image_observations tables for InvisibleID evolution."""
    # image_assets: immutable image records (once stored, never changes)
    op.create_table(
        "image_assets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("sha256_hash", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("mime_type", sa.String(50), nullable=True),
        sa.Column("perceptual_hash", sa.String(64), nullable=True),
        sa.Column("dhash", sa.String(16), nullable=True),
        sa.Column("phash", sa.String(16), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")
        ),
    )
    op.create_index("idx_image_assets_phash", "image_assets", ["perceptual_hash"])
    op.create_index("idx_image_assets_dhash", "image_assets", ["dhash"])

    # image_observations: mutable URL associations to assets (tracks WHERE an image was seen)
    op.create_table(
        "image_observations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("image_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("source_page_url", sa.Text(), nullable=True),
        sa.Column("source_domain", sa.String(255), nullable=True),
        sa.Column("crawl_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "observed_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")
        ),
    )
    op.create_index("idx_observations_asset", "image_observations", ["image_asset_id"])
    op.create_index("idx_observations_url", "image_observations", ["url"])
    op.create_index("idx_observations_domain", "image_observations", ["source_domain"])

    # Add FK constraints
    op.create_foreign_key(
        "fk_observations_asset",
        "image_observations",
        "image_assets",
        ["image_asset_id"],
        ["id"],
    )

    # invisibleid_detections: future detection results
    op.create_table(
        "invisibleid_detections",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("image_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("detection_confidence", sa.Float(), nullable=True),
        sa.Column("watermark_type", sa.String(50), nullable=True),
        sa.Column(
            "detected_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("detector_version", sa.String(20), nullable=True),
    )
    op.create_index("idx_detections_asset", "invisibleid_detections", ["image_asset_id"])

    # Add FK constraint
    op.create_foreign_key(
        "fk_detections_asset",
        "invisibleid_detections",
        "image_assets",
        ["image_asset_id"],
        ["id"],
    )


def downgrade() -> None:
    """Downgrade schema - remove new tables."""
    op.drop_constraint("fk_detections_asset", "invisibleid_detections", type_="foreignkey")
    op.drop_index("idx_detections_asset", table_name="invisibleid_detections")
    op.drop_table("invisibleid_detections")

    op.drop_constraint("fk_observations_asset", "image_observations", type_="foreignkey")
    op.drop_index("idx_observations_domain", table_name="image_observations")
    op.drop_index("idx_observations_url", table_name="image_observations")
    op.drop_index("idx_observations_asset", table_name="image_observations")
    op.drop_table("image_observations")

    op.drop_index("idx_image_assets_dhash", table_name="image_assets")
    op.drop_index("idx_image_assets_phash", table_name="image_assets")
    op.drop_table("image_assets")
