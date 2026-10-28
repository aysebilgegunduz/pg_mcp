"""Integration tests for discover_schema_for_question against real pgvector.

A knowledge map and two golden queries are created, then discovery must return
both schema metadata and golden queries in a single call.
"""

import pytest
import pytest_asyncio
from semantic_db import create_customer_schema, run_sql

from pg_airman_mcp.semantics.discovery_tools import discover_schema_for_question
from pg_airman_mcp.semantics.golden_query_tools import (
    create_golden_query,
    delete_golden_query,
)
from pg_airman_mcp.semantics.knowledge_map_tools import (
    create_knowledge_map,
    delete_knowledge_map,
)

MAP_NAME = "mcp_discovery_test_map"
SCHEMA = "mcp_discovery_test_schema"
GOLDEN_QUERIES = ["mcp_discovery_test_revenue", "mcp_discovery_test_customers"]


@pytest_asyncio.fixture
async def sample_map(sql_driver, embedder):
    await create_customer_schema(sql_driver, SCHEMA)
    await create_knowledge_map(sql_driver, embedder, MAP_NAME, [SCHEMA])
    await create_golden_query(
        sql_driver,
        embedder,
        MAP_NAME,
        "mcp_discovery_test_revenue",
        "Calculate total revenue from completed orders",
        f"SELECT SUM(total_amount) AS total_revenue FROM {SCHEMA}.orders "
        "WHERE status = 'completed'",
    )
    await create_golden_query(
        sql_driver,
        embedder,
        MAP_NAME,
        "mcp_discovery_test_customers",
        "List all customers with their email addresses",
        f"SELECT first_name, last_name, email FROM {SCHEMA}.customers "
        "ORDER BY last_name",
    )
    yield {"map_name": MAP_NAME, "schema_name": SCHEMA}

    for name in GOLDEN_QUERIES:
        await delete_golden_query(sql_driver, name)
    await delete_knowledge_map(sql_driver, MAP_NAME)
    await run_sql(sql_driver, f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


async def discover(driver, embedder, question, **kwargs):
    kwargs.setdefault("min_similarity", 0.0)
    kwargs.setdefault("max_results", 50)
    return await discover_schema_for_question(
        driver, embedder, MAP_NAME, question, **kwargs
    )


@pytest.mark.asyncio
async def test_discover_returns_schema_and_golden_queries(
    sql_driver, sample_map, embedder
):
    results = await discover(sql_driver, embedder, "customer orders revenue")

    assert results["schema_metadata"]
    assert {a["name"] for a in results["golden_queries"]} == set(GOLDEN_QUERIES)


@pytest.mark.asyncio
async def test_discover_result_fields(sql_driver, sample_map, embedder):
    results = await discover(sql_driver, embedder, "customer email")

    entry = results["schema_metadata"][0]
    assert {
        "schema_name",
        "relation_name",
        "column_name",
        "entity_type",
        "definition",
        "comment",
        "similarity",
    } <= entry.keys()
    golden = results["golden_queries"][0]
    assert {"name", "description", "query_text", "parameters", "similarity"} <= (
        golden.keys()
    )


@pytest.mark.asyncio
async def test_discover_ranks_relevant_items_first(sql_driver, sample_map, embedder):
    results = await discover(sql_driver, embedder, "total revenue of orders")

    assert results["golden_queries"][0]["name"] == "mcp_discovery_test_revenue"
    top_tables = [
        r["relation_name"]
        for r in results["schema_metadata"]
        if r["entity_type"] == "Table"
    ]
    assert top_tables[0] == "orders"


@pytest.mark.asyncio
async def test_discover_without_golden_queries(sql_driver, sample_map, embedder):
    results = await discover(
        sql_driver, embedder, "customer", include_golden_queries=False
    )

    assert results["schema_metadata"]
    assert results["golden_queries"] == []


@pytest.mark.asyncio
async def test_discover_respects_max_results(sql_driver, sample_map, embedder):
    results = await discover(sql_driver, embedder, "customer", max_results=2)

    assert len(results["schema_metadata"]) == 2
    assert len(results["golden_queries"]) == 2


@pytest.mark.asyncio
async def test_discover_high_similarity_filters_results(
    sql_driver, sample_map, embedder
):
    low = await discover(sql_driver, embedder, "customer", min_similarity=0.0)
    high = await discover(sql_driver, embedder, "customer", min_similarity=0.99)

    assert len(high["schema_metadata"]) < len(low["schema_metadata"])
    assert high["golden_queries"] == []


@pytest.mark.asyncio
async def test_discover_works_in_restricted_mode(safe_driver, sample_map, embedder):
    results = await discover(safe_driver, embedder, "customer orders")

    assert results["schema_metadata"]
    assert results["golden_queries"]


@pytest.mark.asyncio
async def test_discover_without_golden_queries_restricted(
    safe_driver, sample_map, embedder
):
    results = await discover(
        safe_driver, embedder, "orders", include_golden_queries=False
    )

    assert results["schema_metadata"]
    assert results["golden_queries"] == []
