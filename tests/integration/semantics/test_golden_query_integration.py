"""Integration tests for golden query tools against a real pgvector database.

Besides the CRUD/search/execute flows, these tests verify the execution
hardening end to end: bound parameters, re-validation of stored SQL, READ
ONLY transactions, transaction-scoped role switching and restricted-mode
statement timeouts.
"""

import logging
from unittest.mock import patch

import psycopg
import pytest
import pytest_asyncio
from semantic_db import create_customer_schema, run_sql

from pgpacer.semantics.golden_query_tools import (
    create_golden_query,
    delete_golden_query,
    execute_golden_query,
    list_golden_queries,
    search_golden_queries,
)
from pgpacer.semantics.knowledge_map_tools import (
    create_knowledge_map,
    delete_knowledge_map,
)
from pgpacer.sql import SafeSqlDriver

logger = logging.getLogger(__name__)

MAP_NAME = "mcp_golden_query_test_map"
SCHEMA = "mcp_golden_query_test_schema"
GOLDEN_QUERIES = [
    "mcp_test_customer_by_email",
    "mcp_test_order_totals",
    "mcp_test_all_customers",
    "mcp_test_customers_by_id",
    "mcp_test_scratch",
]
EMAIL_PARAM = [{"name": "email", "param_type": "text", "description": "Email"}]


@pytest_asyncio.fixture
async def sample_map(sql_driver, embedder):
    await create_customer_schema(sql_driver, SCHEMA)
    await create_knowledge_map(sql_driver, embedder, MAP_NAME, [SCHEMA])
    yield {"map_name": MAP_NAME, "schema_name": SCHEMA}

    for name in GOLDEN_QUERIES:
        try:
            await delete_golden_query(sql_driver, name)
        except ValueError:
            pass  # not created by this test
    await delete_knowledge_map(sql_driver, MAP_NAME)
    await run_sql(sql_driver, f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


async def create(driver, embedder, name, description, query_text, parameters=None):
    return await create_golden_query(
        driver, embedder, MAP_NAME, name, description, query_text, parameters
    )


async def create_email_query(driver, embedder):
    await create(
        driver,
        embedder,
        "mcp_test_customer_by_email",
        "Find a customer by their email address",
        f"SELECT id, email, first_name FROM {SCHEMA}.customers "
        "WHERE email = ${email}",
        EMAIL_PARAM,
    )


# ============================================
# create / list / delete
# ============================================


@pytest.mark.asyncio
async def test_create_golden_query_basic(sql_driver, sample_map, embedder):
    result = await create(
        sql_driver,
        embedder,
        "mcp_test_all_customers",
        "List all customers in the system",
        f"SELECT id, email, first_name, last_name FROM {SCHEMA}.customers",
    )

    assert result == "Created golden query 'mcp_test_all_customers'"


@pytest.mark.asyncio
async def test_create_golden_query_stores_model_and_vector(
    sql_driver, sample_map, embedder
):
    await create_email_query(sql_driver, embedder)

    rows = await sql_driver.execute_query(
        """
        SELECT model_name, dimensions, vector_dims(embedding) AS dims, parameters
        FROM pgpacer.golden_queries WHERE name = %s
        """,
        ["mcp_test_customer_by_email"],
    )

    assert rows
    stored = rows[0].cells
    assert (stored["model_name"], stored["dimensions"], stored["dims"]) == (
        "pgpacer-hash-v1",
        256,
        256,
    )
    assert stored["parameters"] == EMAIL_PARAM


@pytest.mark.asyncio
async def test_create_golden_query_duplicate_fails(sql_driver, sample_map, embedder):
    query = f"SELECT customer_id, SUM(total_amount) FROM {SCHEMA}.orders GROUP BY 1"
    await create(sql_driver, embedder, "mcp_test_order_totals", "Order totals", query)

    with pytest.raises(ValueError, match="already exists"):
        await create(sql_driver, embedder, "mcp_test_order_totals", "Dup", "SELECT 1")


@pytest.mark.parametrize(
    "query_text",
    [
        f"DELETE FROM {SCHEMA}.orders",
        f"WITH d AS (DELETE FROM {SCHEMA}.orders RETURNING *) SELECT count(*) FROM d",
        f"SELECT * INTO {SCHEMA}.stolen FROM {SCHEMA}.customers",
        f"SELECT 1; DROP TABLE {SCHEMA}.orders",
    ],
)
@pytest.mark.asyncio
async def test_create_rejects_non_read_only_sql(
    sql_driver, sample_map, embedder, query_text
):
    with pytest.raises(ValueError):
        await create(sql_driver, embedder, "mcp_test_scratch", "Bad", query_text)

    rows = await sql_driver.execute_query(f"SELECT count(*) AS n FROM {SCHEMA}.orders")
    assert rows is not None and rows[0].cells["n"] == 3


@pytest.mark.asyncio
async def test_list_and_delete_golden_query(sql_driver, sample_map, embedder):
    await create(
        sql_driver,
        embedder,
        "mcp_test_all_customers",
        "List all customers",
        f"SELECT * FROM {SCHEMA}.customers",
    )

    listed = {a["name"]: a for a in await list_golden_queries(sql_driver)}
    assert listed["mcp_test_all_customers"]["param_count"] == 0

    result = await delete_golden_query(sql_driver, "mcp_test_all_customers")
    assert result == "Deleted golden query 'mcp_test_all_customers'"
    names = [a["name"] for a in await list_golden_queries(sql_driver)]
    assert "mcp_test_all_customers" not in names

    with pytest.raises(ValueError, match="not found"):
        await delete_golden_query(sql_driver, "mcp_test_all_customers")


# ============================================
# search
# ============================================


@pytest.mark.asyncio
async def test_search_golden_queries_finds_match(sql_driver, sample_map, embedder):
    await create_email_query(sql_driver, embedder)
    await create(
        sql_driver,
        embedder,
        "mcp_test_order_totals",
        "Total order amounts per customer",
        f"SELECT customer_id, SUM(total_amount) FROM {SCHEMA}.orders GROUP BY 1",
    )

    results = await search_golden_queries(
        sql_driver, embedder, MAP_NAME, "find customer by email", 0.0, 10
    )

    assert results[0]["name"] == "mcp_test_customer_by_email"
    assert results[0]["parameters"] == EMAIL_PARAM
    assert results[0]["similarity"] > results[1]["similarity"]


@pytest.mark.asyncio
async def test_search_golden_queries_no_results(sql_driver, sample_map, embedder):
    await create_email_query(sql_driver, embedder)

    results = await search_golden_queries(
        sql_driver,
        embedder,
        MAP_NAME,
        "completely unrelated xyz quantum physics",
        0.99,
        10,
    )

    assert results == []


# ============================================
# execute
# ============================================


@pytest.mark.asyncio
async def test_execute_golden_query_no_params(sql_driver, sample_map, embedder):
    await create(
        sql_driver,
        embedder,
        "mcp_test_all_customers",
        "List all customers",
        f"SELECT id, email FROM {SCHEMA}.customers ORDER BY id",
    )

    results = await execute_golden_query(sql_driver, "mcp_test_all_customers")

    assert [r["email"] for r in results] == ["alice@example.com", "bob@example.com"]


@pytest.mark.asyncio
async def test_execute_golden_query_with_params(sql_driver, sample_map, embedder):
    await create_email_query(sql_driver, embedder)

    results = await execute_golden_query(
        sql_driver, "mcp_test_customer_by_email", {"email": "alice@example.com"}
    )

    assert len(results) == 1
    assert results[0]["first_name"] == "Alice"


@pytest.mark.asyncio
async def test_execute_with_typed_array_and_percent_literal(
    sql_driver, sample_map, embedder
):
    await create(
        sql_driver,
        embedder,
        "mcp_test_customers_by_id",
        "Customers by id list",
        f"SELECT first_name FROM {SCHEMA}.customers "
        "WHERE id = ANY(${ids}) AND email LIKE '%@example.com' ORDER BY id",
        [{"name": "ids", "param_type": "integer[]"}],
    )

    results = await execute_golden_query(
        sql_driver, "mcp_test_customers_by_id", {"ids": [2, "1"]}
    )

    assert [r["first_name"] for r in results] == ["Alice", "Bob"]


@pytest.mark.asyncio
async def test_execute_rejects_value_of_wrong_type(sql_driver, sample_map, embedder):
    await create(
        sql_driver,
        embedder,
        "mcp_test_customers_by_id",
        "Customers by id list",
        f"SELECT * FROM {SCHEMA}.customers WHERE id = ANY(${{ids}})",
        [{"name": "ids", "param_type": "integer[]"}],
    )

    with pytest.raises(psycopg.errors.InvalidTextRepresentation):
        await execute_golden_query(
            sql_driver, "mcp_test_customers_by_id", {"ids": ["1 OR 1=1"]}
        )


@pytest.mark.asyncio
async def test_sql_injection_in_parameter_is_inert(sql_driver, sample_map, embedder):
    await create_email_query(sql_driver, embedder)

    for payload in [
        "x' OR '1'='1",
        f"x'; DROP TABLE {SCHEMA}.orders; --",
        "alice@example.com' UNION SELECT 1, 'pwned', 'x' --",
    ]:
        results = await execute_golden_query(
            sql_driver, "mcp_test_customer_by_email", {"email": payload}
        )
        assert results == []

    rows = await sql_driver.execute_query(f"SELECT count(*) AS n FROM {SCHEMA}.orders")
    assert rows is not None and rows[0].cells["n"] == 3


@pytest.mark.asyncio
async def test_execute_golden_query_nonexistent(sql_driver, sample_map):
    with pytest.raises(ValueError, match="not found"):
        await execute_golden_query(sql_driver, "nonexistent_golden_query_xyz")


@pytest.mark.asyncio
async def test_tampered_stored_golden_query_is_not_executed(
    sql_driver, sample_map, embedder
):
    """Rows edited directly in the database are revalidated before running."""
    await create_email_query(sql_driver, embedder)
    await sql_driver.execute_query(
        "UPDATE pgpacer.golden_queries SET query_text = %s, parameters = '[]' "
        "WHERE name = %s",
        [
            f"WITH d AS (DELETE FROM {SCHEMA}.orders RETURNING *) SELECT * FROM d",
            "mcp_test_customer_by_email",
        ],
    )

    with pytest.raises(ValueError, match="read-only"):
        await execute_golden_query(sql_driver, "mcp_test_customer_by_email")

    rows = await sql_driver.execute_query(f"SELECT count(*) AS n FROM {SCHEMA}.orders")
    assert rows is not None and rows[0].cells["n"] == 3


@pytest.mark.asyncio
async def test_execution_runs_in_read_only_transaction(
    sql_driver, sample_map, embedder
):
    """Even a SELECT with write side effects is blocked by the database."""
    await run_sql(sql_driver, f"CREATE SEQUENCE {SCHEMA}.counter")
    await create(
        sql_driver,
        embedder,
        "mcp_test_scratch",
        "Advance a sequence",
        f"SELECT nextval('{SCHEMA}.counter')",
    )

    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        await execute_golden_query(sql_driver, "mcp_test_scratch")


@pytest.mark.asyncio
async def test_execute_as_role_is_enforced_and_scoped(sql_driver, sample_map, embedder):
    role = "mcp_golden_query_reader"
    await run_sql(
        sql_driver,
        f"DROP ROLE IF EXISTS {role}",
        f"CREATE ROLE {role} NOLOGIN",
        f"GRANT USAGE ON SCHEMA {SCHEMA} TO {role}",
        f"GRANT SELECT ON {SCHEMA}.customers TO {role}",
    )
    try:
        await create(
            sql_driver,
            embedder,
            "mcp_test_all_customers",
            "Who am I and which customers exist",
            f"SELECT current_user AS who, count(*) AS n FROM {SCHEMA}.customers",
        )
        await create(
            sql_driver,
            embedder,
            "mcp_test_order_totals",
            "Order totals",
            f"SELECT sum(total_amount) AS total FROM {SCHEMA}.orders",
        )

        rows = await execute_golden_query(
            sql_driver, "mcp_test_all_customers", execute_as_role=role
        )
        assert rows == [{"who": role, "n": 2}]

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await execute_golden_query(
                sql_driver, "mcp_test_order_totals", execute_as_role=role
            )

        # SET LOCAL ROLE must not leak onto pooled connections.
        for _ in range(5):
            rows = await sql_driver.execute_query("SELECT current_user AS who")
            assert rows is not None and rows[0].cells["who"] == "postgres"

        with pytest.raises(psycopg.errors.InvalidParameterValue):
            await execute_golden_query(
                sql_driver, "mcp_test_all_customers", execute_as_role="no_such_role"
            )
    finally:
        await run_sql(
            sql_driver,
            f"REVOKE ALL ON {SCHEMA}.customers FROM {role}",
            f"REVOKE ALL ON SCHEMA {SCHEMA} FROM {role}",
            f"DROP ROLE {role}",
        )


# ============================================
# Restricted mode (SafeSqlDriver)
# ============================================


@pytest.mark.asyncio
async def test_search_and_list_restricted(
    safe_driver, sql_driver, sample_map, embedder
):
    await create_email_query(sql_driver, embedder)

    found = await search_golden_queries(
        safe_driver, embedder, MAP_NAME, "customer email", 0.0, 10
    )
    listed = await list_golden_queries(safe_driver)

    assert "mcp_test_customer_by_email" in [a["name"] for a in found]
    assert "mcp_test_customer_by_email" in [a["name"] for a in listed]


@pytest.mark.asyncio
async def test_execute_restricted(safe_driver, sql_driver, sample_map, embedder):
    await create_email_query(sql_driver, embedder)

    results = await execute_golden_query(
        safe_driver, "mcp_test_customer_by_email", {"email": "bob@example.com"}
    )

    assert [r["first_name"] for r in results] == ["Bob"]


@pytest.mark.asyncio
async def test_restricted_mode_applies_function_allowlist(
    safe_driver, sql_driver, sample_map, embedder
):
    # Valid as an unrestricted golden query, but pg_sleep is not allowed in
    # restricted mode.
    await create(
        sql_driver, embedder, "mcp_test_scratch", "Sleep", "SELECT pg_sleep(0)"
    )

    with pytest.raises(ValueError, match="Error validating query"):
        await execute_golden_query(safe_driver, "mcp_test_scratch")


@pytest.mark.asyncio
async def test_restricted_mode_enforces_statement_timeout(
    pgvector_dsn, sql_driver, sample_map, embedder
):
    await create(
        sql_driver,
        embedder,
        "mcp_test_scratch",
        "Count to a very large number",
        "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r "
        "WHERE n < 1000000000) SELECT count(*) FROM r",
    )
    fast_timeout = SafeSqlDriver(sql_driver=sql_driver, timeout=1)

    with pytest.raises(psycopg.errors.QueryCanceled):
        await execute_golden_query(fast_timeout, "mcp_test_scratch")


@pytest.mark.asyncio
async def test_golden_query_writes_blocked_in_restricted_mode_by_default(
    safe_driver, sample_map, embedder
):
    with pytest.raises(ValueError, match="not permitted in restricted mode"):
        await create(safe_driver, embedder, "mcp_test_scratch", "x", "SELECT 1")
    with pytest.raises(ValueError, match="not permitted in restricted mode"):
        await delete_golden_query(safe_driver, "mcp_test_scratch")


@pytest.mark.asyncio
async def test_golden_query_writes_allowed_in_restricted_mode_with_flag(
    safe_driver, sample_map, embedder
):
    flag = "pgpacer.semantics.golden_query_tools.ALLOW_GOLDEN_QUERY_WRITE_IN_RESTRICTED"
    with patch(flag, True):
        await create(
            safe_driver,
            embedder,
            "mcp_test_scratch",
            "Customer count",
            f"SELECT count(*) FROM {SCHEMA}.customers",
        )
        assert await delete_golden_query(safe_driver, "mcp_test_scratch")
