"""Integration tests for knowledge map tools against a real pgvector database.

Each test builds a schema with two tables and a view, creates a knowledge
map over it with the deterministic hash embedder, and cleans up afterwards.
"""

import asyncio
import logging

import pytest
import pytest_asyncio
from semantic_db import create_customer_schema, run_sql

from pg_airman_mcp.semantics.embeddings import HashEmbeddingProvider
from pg_airman_mcp.semantics.knowledge_map_tools import (
    create_knowledge_map,
    delete_knowledge_map,
    get_knowledge_map_stats,
    list_knowledge_maps,
    refresh_knowledge_map,
    search_knowledge_map,
)
from pg_airman_mcp.semantics.store import ensure_installed
from pg_airman_mcp.sql import SafeSqlDriver, SqlDriver

logger = logging.getLogger(__name__)

MAP_NAME = "mcp_test_map"
SCHEMA = "mcp_test_schema"


@pytest_asyncio.fixture
async def sample_map(sql_driver, embedder):
    """Create customers, orders and a view, then a knowledge map over the schema."""
    await create_customer_schema(sql_driver, SCHEMA)
    await run_sql(
        sql_driver,
        f"""CREATE VIEW {SCHEMA}.customer_orders AS
            SELECT c.id, c.email, COUNT(o.id) AS order_count
            FROM {SCHEMA}.customers c
            LEFT JOIN {SCHEMA}.orders o ON c.id = o.customer_id
            GROUP BY c.id, c.email""",
        f"COMMENT ON VIEW {SCHEMA}.customer_orders IS 'Customer order summary'",
    )
    await create_knowledge_map(sql_driver, embedder, MAP_NAME, [SCHEMA])
    yield {"map_name": MAP_NAME, "schema_name": SCHEMA}

    try:
        await delete_knowledge_map(sql_driver, MAP_NAME)
    except ValueError:
        pass  # deleted by the test
    await run_sql(sql_driver, f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


async def search(driver, embedder, query="customer", **kwargs):
    kwargs.setdefault("min_similarity", 0.0)
    kwargs.setdefault("limit", 100)
    return await search_knowledge_map(driver, embedder, MAP_NAME, query, **kwargs)


# ============================================
# create / list / stats
# ============================================


@pytest.mark.asyncio
async def test_list_knowledge_maps(sql_driver, sample_map):
    results = await list_knowledge_maps(sql_driver)

    ours = next(r for r in results if r["name"] == MAP_NAME)
    assert ours["model_name"] == "airman-hash-v1"
    assert ours["dimensions"] == 256
    assert ours["schemas"] == [SCHEMA]
    assert ours["auto_processing"] == "Disabled"


@pytest.mark.asyncio
async def test_get_knowledge_map_stats(sql_driver, sample_map):
    """customers (4 cols), orders (4 cols), customer_orders view (3 cols)."""
    result = await get_knowledge_map_stats(sql_driver, MAP_NAME)

    assert result["tables"] == 2
    assert result["views"] == 1
    assert result["columns"] == 11
    assert result["total"] == 14
    assert result["pending"] == 0


@pytest.mark.asyncio
async def test_get_knowledge_map_stats_nonexistent(sql_driver, sample_map):
    with pytest.raises(ValueError, match="'nonexistent_map_xyz' not found"):
        await get_knowledge_map_stats(sql_driver, "nonexistent_map_xyz")


@pytest.mark.asyncio
async def test_create_duplicate_map_fails(sql_driver, sample_map, embedder):
    with pytest.raises(ValueError, match="already exists"):
        await create_knowledge_map(sql_driver, embedder, MAP_NAME, [SCHEMA])


@pytest.mark.asyncio
async def test_create_map_with_missing_schema_fails(sql_driver, sample_map, embedder):
    with pytest.raises(ValueError, match="not found: no_such_schema"):
        await create_knowledge_map(
            sql_driver, embedder, "other_map", ["no_such_schema"]
        )


@pytest.mark.asyncio
async def test_concurrent_schema_install_is_safe(sql_driver, pgvector_dsn):
    """Several servers installing the storage schema at once must not fail."""
    await run_sql(sql_driver, "DROP SCHEMA IF EXISTS airman_semantic CASCADE")
    # One driver (pool) per simulated server process, each already connected.
    drivers = [SqlDriver(engine_url=pgvector_dsn) for _ in range(3)]
    for driver in drivers:
        driver.connect()
        await driver.execute_query("SELECT 1")
    try:
        await asyncio.gather(*(ensure_installed(d) for d in drivers))
        assert await list_knowledge_maps(sql_driver) == []
    finally:
        for driver in drivers:
            if driver.conn is not None:
                await driver.conn.close()


# ============================================
# search
# ============================================


@pytest.mark.asyncio
async def test_search_knowledge_map_all_entities(sql_driver, sample_map, embedder):
    results = await search(sql_driver, embedder)

    assert len(results) == 14
    assert {r["entity_type"] for r in results} == {"Table", "View", "Column"}
    assert all(r["schema_name"] == SCHEMA for r in results)
    similarities = [r["similarity"] for r in results]
    assert similarities == sorted(similarities, reverse=True)


@pytest.mark.asyncio
async def test_search_knowledge_map_returns_column_name(
    sql_driver, sample_map, embedder
):
    results = await search(sql_driver, embedder)

    for row in results:
        if row["entity_type"] == "Column":
            assert row["column_name"]
        else:
            assert row["column_name"] is None


@pytest.mark.asyncio
async def test_search_knowledge_map_ranks_relevant_column_first(
    sql_driver, sample_map, embedder
):
    results = await search(sql_driver, embedder, "customer email address", limit=3)

    assert (results[0]["relation_name"], results[0]["column_name"]) == (
        "customers",
        "email",
    )
    assert "Customer email address" in results[0]["definition"]


@pytest.mark.asyncio
async def test_search_knowledge_map_tables_only(sql_driver, sample_map, embedder):
    results = await search(sql_driver, embedder, entity_types=["Table"])

    assert {r["relation_name"] for r in results} == {"customers", "orders"}
    assert all(r["entity_type"] == "Table" for r in results)


@pytest.mark.asyncio
async def test_search_knowledge_map_column_filter(sql_driver, sample_map, embedder):
    results = await search(sql_driver, embedder, entity_types=["Column"])

    assert len(results) == 11
    assert all(r["entity_type"] == "Column" and r["column_name"] for r in results)


@pytest.mark.asyncio
async def test_search_knowledge_map_mixed_filter(sql_driver, sample_map, embedder):
    results = await search(sql_driver, embedder, entity_types=["Column", "view"])

    assert {r["entity_type"] for r in results} == {"Column", "View"}


@pytest.mark.asyncio
async def test_search_knowledge_map_limit(sql_driver, sample_map, embedder):
    assert len(await search(sql_driver, embedder, limit=3)) == 3


@pytest.mark.asyncio
async def test_search_knowledge_map_min_similarity(sql_driver, sample_map, embedder):
    results = await search(sql_driver, embedder, "zebra quantum", min_similarity=0.5)

    assert results == []


@pytest.mark.asyncio
async def test_search_with_different_model_is_rejected(sql_driver, sample_map):
    with pytest.raises(ValueError, match="256-dimensional"):
        await search(sql_driver, HashEmbeddingProvider(dimensions=128))


# ============================================
# refresh
# ============================================


@pytest.mark.asyncio
async def test_refresh_without_changes_is_a_no_op(sql_driver, sample_map, embedder):
    result = await refresh_knowledge_map(sql_driver, embedder, MAP_NAME)

    assert result == (
        f"Refreshed knowledge map '{MAP_NAME}': 0 added, 0 updated, 0 removed"
    )


@pytest.mark.asyncio
async def test_refresh_applies_schema_changes(sql_driver, sample_map, embedder):
    await run_sql(
        sql_driver,
        f"ALTER TABLE {SCHEMA}.customers ADD COLUMN loyalty_tier text",
        f"COMMENT ON COLUMN {SCHEMA}.customers.loyalty_tier IS 'Loyalty program tier'",
        f"COMMENT ON COLUMN {SCHEMA}.orders.status IS 'Order fulfilment status'",
        f"DROP VIEW {SCHEMA}.customer_orders",
    )

    stats = await get_knowledge_map_stats(sql_driver, MAP_NAME)
    # +1 column, customers table (column list), orders.status, -1 view, -3 cols
    assert stats["pending"] == 7

    result = await refresh_knowledge_map(sql_driver, embedder, MAP_NAME)
    assert result.endswith("1 added, 2 updated, 4 removed")

    stats = await get_knowledge_map_stats(sql_driver, MAP_NAME)
    assert stats["pending"] == 0
    assert (stats["views"], stats["columns"]) == (0, 9)

    top = (await search(sql_driver, embedder, "loyalty program tier", limit=1))[0]
    assert top["column_name"] == "loyalty_tier"


@pytest.mark.asyncio
async def test_delete_map_removes_entities(sql_driver, sample_map):
    await delete_knowledge_map(sql_driver, MAP_NAME)

    assert MAP_NAME not in [r["name"] for r in await list_knowledge_maps(sql_driver)]
    rows = await sql_driver.execute_query(
        "SELECT count(*) AS n FROM airman_semantic.knowledge_map_entries "
        "WHERE map_name = %s",
        [MAP_NAME],
    )
    assert rows is not None and rows[0].cells["n"] == 0


# ============================================
# Restricted mode (SafeSqlDriver)
# ============================================


@pytest.mark.asyncio
async def test_list_maps_restricted(safe_driver, sample_map):
    results = await list_knowledge_maps(safe_driver)

    assert MAP_NAME in [r["name"] for r in results]


@pytest.mark.asyncio
async def test_get_map_stats_restricted(safe_driver, sample_map):
    result = await get_knowledge_map_stats(safe_driver, MAP_NAME)

    assert (result["tables"], result["views"], result["pending"]) == (2, 1, 0)


@pytest.mark.parametrize("entity_types", [None, ["Table"], ["Column"]])
@pytest.mark.asyncio
async def test_search_knowledge_map_restricted(
    safe_driver, sample_map, embedder, entity_types
):
    results = await search(safe_driver, embedder, entity_types=entity_types)

    assert results
    if entity_types:
        assert {r["entity_type"] for r in results} == set(entity_types)


@pytest.mark.asyncio
async def test_map_writes_rejected_in_restricted_mode(
    safe_driver, sample_map, embedder
):
    with pytest.raises(ValueError, match="not permitted in restricted mode"):
        await refresh_knowledge_map(safe_driver, embedder, MAP_NAME)
    with pytest.raises(ValueError, match="not permitted in restricted mode"):
        await create_knowledge_map(safe_driver, embedder, "other_map", [SCHEMA])
    with pytest.raises(ValueError, match="not permitted in restricted mode"):
        await delete_knowledge_map(safe_driver, MAP_NAME)


@pytest.mark.asyncio
async def test_least_privilege_reader_can_search(
    sql_driver, sample_map, embedder, pgvector_dsn
):
    """The GRANTs documented in the README are sufficient for restricted mode."""
    role = "mcp_map_reader"
    await run_sql(
        sql_driver,
        f"DROP ROLE IF EXISTS {role}",
        f"CREATE ROLE {role} LOGIN",
        f"GRANT USAGE ON SCHEMA airman_semantic TO {role}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA airman_semantic TO {role}",
    )
    reader = SqlDriver(engine_url=pgvector_dsn.replace("postgres@", f"{role}@", 1))
    reader.connect()
    try:
        results = await search(SafeSqlDriver(reader, timeout=30), embedder, limit=5)
        assert len(results) == 5
    finally:
        if reader.conn is not None:
            await reader.conn.close()
        await run_sql(
            sql_driver,
            f"REVOKE ALL ON ALL TABLES IN SCHEMA airman_semantic FROM {role}",
            f"REVOKE ALL ON SCHEMA airman_semantic FROM {role}",
            f"DROP ROLE {role}",
        )
