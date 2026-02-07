"""add_domains_table_phase_a

Revision ID: 1c3fe655e18f
Revises: 3b65381b0f4e
Create Date: 2026-02-06 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "1c3fe655e18f"
down_revision: str | Sequence[str] | None = "3b65381b0f4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema - add domains table with full schema for Phase A."""
    # Use raw SQL to create enum (checkfirst doesn't work well in transactions)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'domain_status') THEN
                CREATE TYPE domain_status AS ENUM ('pending', 'active', 'exhausted', 'blocked', 'unreachable');
            END IF;
        END
        $$;
    """)

    # Create domains table
    op.create_table(
        "domains",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("domain", sa.String(255), nullable=False, unique=True),
        # Discovery state
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending",
                "active",
                "exhausted",
                "blocked",
                "unreachable",
                name="domain_status",
                create_type=False,  # Already created above
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("block_reason", sa.Text(), nullable=True),
        sa.Column("block_reason_code", sa.String(50), nullable=True),
        sa.Column("first_blocked_at", sa.DateTime(timezone=True), nullable=True),
        # Concurrency control (for future phases)
        sa.Column("claimed_by", sa.String(255), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # Crawl progress (cumulative across all runs)
        sa.Column("pages_discovered", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("pages_crawled", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("images_found", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("images_stored", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        # Crawl budget
        sa.Column("max_pages_per_run", sa.Integer(), nullable=True, server_default=sa.text("1000")),
        sa.Column("crawl_depth_reached", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # Quality signals
        sa.Column("image_yield_rate", sa.Double(), nullable=True),
        sa.Column("avg_images_per_page", sa.Double(), nullable=True),
        sa.Column("error_rate", sa.Double(), nullable=True),
        sa.Column("total_error_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "consecutive_error_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        # Scheduling
        sa.Column("priority_score", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("priority_computed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seed_rank", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(100), nullable=True),
        # Timestamps
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("last_crawled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_crawl_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        # Resume support
        sa.Column("last_crawl_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("frontier_checkpoint_id", sa.String(100), nullable=True),
        # Frontier metadata
        sa.Column("frontier_size", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("frontier_updated_at", sa.DateTime(timezone=True), nullable=True),
        # Foreign key to crawl_runs
        sa.ForeignKeyConstraint(
            ["last_crawl_run_id"],
            ["crawl_runs.id"],
            name="fk_domains_last_crawl_run",
            ondelete="SET NULL",
        ),
    )

    # Create indexes for scheduling queries
    op.create_index("idx_domains_status", "domains", ["status"])
    op.create_index(
        "idx_domains_priority_score",
        "domains",
        [sa.text("priority_score DESC")],
    )
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_domains_next_crawl
        ON domains(next_crawl_after)
        WHERE next_crawl_after IS NOT NULL;
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_domains_yield
        ON domains(image_yield_rate DESC NULLS LAST);
    """)
    op.create_index("idx_domains_last_crawled", "domains", ["last_crawled_at"])
    op.create_index("idx_domains_source", "domains", ["source"])

    # Composite indexes for specific queries
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_domains_discovery_candidates
        ON domains(status, next_crawl_after, priority_score DESC)
        WHERE status IN ('pending', 'active');
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_domains_refresh_candidates
        ON domains(status, next_crawl_after, image_yield_rate DESC)
        WHERE status = 'exhausted';
    """)

    # Concurrency indexes
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_domains_claims
        ON domains(claimed_by, claim_expires_at)
        WHERE claimed_by IS NOT NULL;
    """)

    # Trigger to update updated_at timestamp
    op.execute("""
        CREATE OR REPLACE FUNCTION update_domains_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = CURRENT_TIMESTAMP;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER domains_updated_at_trigger
            BEFORE UPDATE ON domains
            FOR EACH ROW
            EXECUTE FUNCTION update_domains_updated_at();
    """)


def downgrade() -> None:
    """Downgrade schema - remove domains table."""
    # Drop trigger and function
    op.execute("DROP TRIGGER IF EXISTS domains_updated_at_trigger ON domains;")
    op.execute("DROP FUNCTION IF EXISTS update_domains_updated_at();")

    # Drop the transition function if it exists (added in later migration, but may be present)
    op.execute("DROP FUNCTION IF EXISTS transition_domain_status(UUID, domain_status, domain_status, VARCHAR, INTEGER);")

    # Drop indexes
    op.execute("DROP INDEX IF EXISTS idx_domains_claims;")
    op.execute("DROP INDEX IF EXISTS idx_domains_refresh_candidates;")
    op.execute("DROP INDEX IF EXISTS idx_domains_discovery_candidates;")
    op.drop_index("idx_domains_source", table_name="domains")
    op.drop_index("idx_domains_last_crawled", table_name="domains")
    op.execute("DROP INDEX IF EXISTS idx_domains_yield;")
    op.execute("DROP INDEX IF EXISTS idx_domains_next_crawl;")
    op.drop_index("idx_domains_priority_score", table_name="domains")
    op.drop_index("idx_domains_status", table_name="domains")

    # Drop domains table
    op.drop_table("domains")

    # Drop enum type
    op.execute("DROP TYPE IF EXISTS domain_status;")
