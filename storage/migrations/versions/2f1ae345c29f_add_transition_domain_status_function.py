"""add_transition_domain_status_function

Revision ID: 2f1ae345c29f
Revises: 1c3fe655e18f
Create Date: 2026-02-07 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2f1ae345c29f"
down_revision: str | Sequence[str] | None = "1c3fe655e18f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema - add transition_domain_status function for safe state transitions."""
    op.execute("""
        CREATE OR REPLACE FUNCTION transition_domain_status(
            p_domain_id UUID,
            p_from_status domain_status,
            p_to_status domain_status,
            p_worker_id VARCHAR(255),
            p_expected_version INTEGER
        )
        RETURNS BOOLEAN AS $$
        DECLARE
            v_row_count INTEGER;
        BEGIN
            -- Valid transitions matrix
            IF NOT (
                (p_from_status = 'pending' AND p_to_status IN ('active', 'unreachable')) OR
                (p_from_status = 'active' AND p_to_status IN ('active', 'exhausted', 'blocked', 'unreachable')) OR
                (p_from_status = 'exhausted' AND p_to_status IN ('pending', 'active')) OR
                (p_from_status = 'blocked' AND p_to_status IN ('pending', 'active')) OR
                (p_from_status = 'unreachable' AND p_to_status IN ('pending', 'active'))
            ) THEN
                RAISE EXCEPTION 'Invalid transition: % -> %', p_from_status, p_to_status;
            END IF;

            -- Attempt transition with optimistic lock
            UPDATE domains
            SET status = p_to_status,
                version = version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = p_domain_id
              AND status = p_from_status
              AND (p_worker_id IS NULL OR claimed_by = p_worker_id)
              AND version = p_expected_version;

            GET DIAGNOSTICS v_row_count = ROW_COUNT;
            RETURN v_row_count > 0;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    """Downgrade schema - remove transition_domain_status function."""
    op.execute(
        "DROP FUNCTION IF EXISTS transition_domain_status(UUID, domain_status, domain_status, VARCHAR, INTEGER);"
    )
