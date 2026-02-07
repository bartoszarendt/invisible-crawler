"""Database connection utility for InvisibleCrawler.

This module provides database connection management using psycopg2
with connection pooling support.
"""

from collections.abc import Generator
from contextlib import contextmanager

import psycopg2
from psycopg2.extensions import connection
from psycopg2.extensions import cursor as psycopg_cursor
from psycopg2.pool import ThreadedConnectionPool

from env_config import get_database_url

DATABASE_URL = get_database_url()

# Global connection pool (initialized lazily)
_connection_pool: ThreadedConnectionPool | None = None


def init_connection_pool(
    min_connections: int = 1, max_connections: int = 10
) -> ThreadedConnectionPool:
    """Initialize the database connection pool.

    Args:
        min_connections: Minimum number of connections to maintain.
        max_connections: Maximum number of connections allowed.

    Returns:
        The initialized connection pool.

    Raises:
        psycopg2.Error: If connection to database fails.
    """
    global _connection_pool

    if _connection_pool is None:
        _connection_pool = ThreadedConnectionPool(
            minconn=min_connections,
            maxconn=max_connections,
            dsn=DATABASE_URL,
        )

    return _connection_pool


@contextmanager
def get_connection() -> Generator[connection, None, None]:
    """Get a database connection from the pool.

    Yields:
        A database connection.

    Example:
        >>> with get_connection() as conn:
        ...     with conn.cursor() as cur:
        ...         cur.execute("SELECT 1")
        ...         result = cur.fetchone()
    """
    pool = init_connection_pool()
    conn = pool.getconn()
    try:
        yield conn
    finally:
        pool.putconn(conn)


@contextmanager
def get_cursor() -> Generator[psycopg_cursor, None, None]:
    """Get a database cursor with automatic connection management.

    Yields:
        A database cursor.

    Example:
        >>> with get_cursor() as cur:
        ...     cur.execute("SELECT 1")
        ...     result = cur.fetchone()
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()


def test_connection() -> bool:
    """Test database connectivity.

    Returns:
        True if connection successful, False otherwise.
    """
    try:
        with get_cursor() as cur:
            cur.execute("SELECT version()")
            version = cur.fetchone()
            print(f"Database connected: {version[0]}")
            return True
    except psycopg2.Error as e:
        print(f"Database connection failed: {e}")
        return False


def close_all_connections() -> None:
    """Close all connections in the pool."""
    global _connection_pool
    if _connection_pool is not None:
        _connection_pool.closeall()
        _connection_pool = None
