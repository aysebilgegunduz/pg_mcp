"""Connection pool behaviour under concurrency against a real PostgreSQL."""

import asyncio

import psycopg
import pytest
import pytest_asyncio

from pgpacer.sql import SqlDriver


@pytest_asyncio.fixture
async def fresh_driver(test_postgres_connection_string):
    """A driver whose pool has not been opened yet."""
    connection_string, _version = test_postgres_connection_string
    driver = SqlDriver(engine_url=connection_string)
    driver.connect()
    try:
        yield driver
    finally:
        if driver.conn is not None:
            await driver.conn.close()


@pytest.mark.asyncio
async def test_concurrent_first_queries_share_one_pool(fresh_driver):
    """Concurrent first use used to build several pools and close each other's."""
    results = await asyncio.gather(
        *(fresh_driver.execute_query("SELECT 1 AS x") for _ in range(5))
    )

    assert all(rows and rows[0].cells["x"] == 1 for rows in results)
    assert fresh_driver.conn.is_valid


@pytest.mark.asyncio
async def test_sql_error_does_not_rebuild_pool(fresh_driver):
    """A bad query must not tear down the pool that concurrent queries use."""
    await fresh_driver.execute_query("SELECT 1")
    pool = fresh_driver.conn.pool

    async def slow_query():
        return await fresh_driver.execute_query("SELECT pg_sleep(0.3), 1 AS x")

    results = await asyncio.gather(
        slow_query(),
        slow_query(),
        fresh_driver.execute_query("SELEC 1"),
        return_exceptions=True,
    )

    assert isinstance(results[2], psycopg.errors.SyntaxError)
    assert all(rows[0].cells["x"] == 1 for rows in results[:2])  # type: ignore[index]
    await fresh_driver.execute_query("SELECT 1")
    assert fresh_driver.conn.is_valid
    assert fresh_driver.conn.pool is pool
