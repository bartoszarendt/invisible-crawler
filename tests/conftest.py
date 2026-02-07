"""Pytest configuration and fixtures for InvisibleCrawler tests.

Provides database fixtures for integration tests.
"""

import os

import psycopg2.extras
import pytest

# Register UUID adapter for psycopg2 to handle Python UUID objects
psycopg2.extras.register_uuid()

# Skip DB tests if no database URL configured
SKIP_DB_TESTS = not os.getenv("DATABASE_URL")


# Module-level connection pool shared across all tests
_pool = None


class _TestCursor:
    """Cursor proxy that adds commit support for test setup steps."""

    def __init__(self, cursor, conn):
        self._cursor = cursor
        self._conn = conn

    def commit(self):
        self._conn.commit()

    def __getattr__(self, name):
        return getattr(self._cursor, name)


def _get_test_pool():
    """Get or create the test connection pool."""
    global _pool
    if _pool is None:
        from storage.db import init_connection_pool

        # Use larger pool for concurrency tests
        _pool = init_connection_pool(min_connections=2, max_connections=20)
    return _pool


def _ensure_transition_function(cursor):
    """Ensure transition_domain_status() exists for DB integration tests."""
    cursor.execute(
        """
        CREATE OR REPLACE FUNCTION transition_domain_status(
            p_domain_id UUID,
            p_from_status domain_status,
            p_to_status domain_status,
            p_worker_id VARCHAR,
            p_expected_version INTEGER
        )
        RETURNS BOOLEAN AS $$
        DECLARE
            v_row_count INTEGER;
        BEGIN
            IF NOT (
                (p_from_status = 'pending' AND p_to_status IN ('active', 'unreachable')) OR
                (p_from_status = 'active' AND p_to_status IN ('active', 'exhausted', 'blocked', 'unreachable')) OR
                (p_from_status = 'exhausted' AND p_to_status IN ('pending', 'active')) OR
                (p_from_status = 'blocked' AND p_to_status IN ('pending', 'active')) OR
                (p_from_status = 'unreachable' AND p_to_status IN ('pending', 'active'))
            ) THEN
                RAISE EXCEPTION 'Invalid transition: % -> %', p_from_status, p_to_status;
            END IF;

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
        """
    )


@pytest.fixture
def db_cursor():
    """Provide a database cursor for integration tests.

    This fixture truncates the domains table to ensure a clean state,
    then provides a cursor. Data is committed so repository functions
    (which use separate connections) can see it.

    Yields:
        Database cursor object
    """
    if SKIP_DB_TESTS:
        pytest.skip("DATABASE_URL not set, skipping DB test")

    pool = _get_test_pool()
    conn = pool.getconn()
    cursor = None
    test_cursor = None
    try:
        # Start transaction
        conn.autocommit = False
        cursor = conn.cursor()

        # Clean domains table to avoid unique constraint violations
        # from previous test runs
        cursor.execute("TRUNCATE TABLE domains CASCADE")
        _ensure_transition_function(cursor)
        conn.commit()

        # Provide cursor wrapper with commit() helper used by DB tests
        test_cursor = _TestCursor(cursor, conn)

        yield test_cursor

        # Rollback any uncommitted test data
        conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.autocommit = True
            pool.putconn(conn)
