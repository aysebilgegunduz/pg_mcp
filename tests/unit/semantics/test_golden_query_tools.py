"""Tests for golden_query_tools module (pgvector backend)."""

import json
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import pytest
from fake_driver import FakeDriver, rows

from pg_airman_mcp.semantics.embeddings import HashEmbeddingProvider
from pg_airman_mcp.semantics.golden_query_tools import (
    create_golden_query,
    delete_golden_query,
    execute_golden_query,
    list_golden_queries,
    search_golden_queries,
)
from pg_airman_mcp.sql import SafeSqlDriver

FLAG = (
    "pg_airman_mcp.semantics.golden_query_tools.ALLOW_GOLDEN_QUERY_WRITE_IN_RESTRICTED"
)
MAP_ROW = {
    "name": "my_map",
    "model_name": "airman-hash-v1",
    "dimensions": 64,
    "schemas": ["public"],
    "refreshed_at": None,
}
EMAIL_QUERY = {
    "query_text": "SELECT * FROM customers WHERE email = ${email}",
    "parameters": [{"name": "email", "param_type": "text", "description": None}],
}


class FakeRawCursor:
    """Stands in for psycopg.AsyncRawCursor and records what was executed."""

    instances: ClassVar[list["FakeRawCursor"]] = []
    result_rows: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, conn, row_factory=None):
        self.conn = conn
        self.execute = AsyncMock()
        self.description = [("col",)]
        self.fetchall = AsyncMock(return_value=list(self.result_rows))
        FakeRawCursor.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def raw_cursor():
    FakeRawCursor.instances = []
    FakeRawCursor.result_rows = [{"id": 1, "email": "alice@example.com"}]
    with patch(
        "pg_airman_mcp.semantics.golden_query_tools.AsyncRawCursor", FakeRawCursor
    ):
        yield FakeRawCursor


INSTALLED = ("to_regclass", rows({"installed": True}))
NOT_INSTALLED = ("to_regclass", rows({"installed": False}))
MAP_LOOKUP = ("FROM airman_semantic.knowledge_maps", rows(MAP_ROW))


@pytest.fixture
def embedder():
    return HashEmbeddingProvider(dimensions=64)


def executed_sql(conn):
    return [call.args[0].as_string() for call in conn.execute.call_args_list]


# ======================
# create_golden_query
# ======================


@pytest.mark.asyncio
async def test_create_golden_query_basic(embedder):
    driver = FakeDriver(
        [
            INSTALLED,
            MAP_LOOKUP,
            ("INSERT INTO airman_semantic.golden_queries", rows({"name": "x"})),
        ]
    )

    result = await create_golden_query(
        driver,
        embedder,
        map_name="my_map",
        name="monthly_revenue",
        description="Calculate total revenue by month",
        query_text=(
            "SELECT date_trunc('month', order_date) AS month,"
            " SUM(total) FROM orders GROUP BY 1"
        ),
    )

    assert result == "Created golden query 'monthly_revenue'"
    params = driver.last_params()
    assert params[0] == "monthly_revenue"
    assert params[1] == "Calculate total revenue by month"
    assert json.loads(params[3]) == []
    assert params[4:6] == ["airman-hash-v1", 64]
    assert params[6].startswith("[")


@pytest.mark.asyncio
async def test_create_golden_query_with_parameters(embedder):
    driver = FakeDriver(
        [
            INSTALLED,
            MAP_LOOKUP,
            ("INSERT INTO airman_semantic.golden_queries", rows({"name": "x"})),
        ]
    )

    await create_golden_query(
        driver,
        embedder,
        map_name="my_map",
        name="customer_by_email",
        description="Find customer by their email address",
        query_text="SELECT * FROM customers WHERE email = ${email}",
        parameters=[
            {"name": "email", "param_type": "TEXT", "description": "Customer email"}
        ],
    )

    params = driver.last_params()
    # The stored template keeps ${...}; types are normalised.
    assert params[2] == "SELECT * FROM customers WHERE email = ${email}"
    assert json.loads(params[3]) == [
        {"name": "email", "param_type": "text", "description": "Customer email"}
    ]


@pytest.mark.asyncio
async def test_create_golden_query_duplicate(embedder):
    driver = FakeDriver(
        [INSTALLED, MAP_LOOKUP, ("INSERT INTO airman_semantic.golden_queries", [])]
    )

    with pytest.raises(ValueError, match="'dup' already exists"):
        await create_golden_query(
            driver, embedder, "my_map", "dup", "Duplicate", "SELECT 1"
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"name": "bad name"}, "Invalid golden query name"),
        ({"description": "  "}, "description must not be empty"),
        ({"description": "x" * 2001}, "description must not exceed"),
        ({"query_text": "DELETE FROM customers"}, "must be a SELECT"),
        (
            {"query_text": "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d"},
            "read-only",
        ),
        ({"query_text": "SELECT ${missing}"}, "not declared"),
        (
            {"parameters": [{"name": "x", "param_type": "text; DROP TABLE t"}]},
            "Unsupported param_type",
        ),
    ],
)
@pytest.mark.asyncio
async def test_create_golden_query_validates_before_db_access(
    embedder, kwargs, message
):
    driver = FakeDriver([])
    args = {
        "map_name": "my_map",
        "name": "query1",
        "description": "Some golden query",
        "query_text": "SELECT 1",
    } | kwargs

    with pytest.raises(ValueError, match=message):
        await create_golden_query(driver, embedder, **args)

    assert driver.calls == []


# ======================
# delete_golden_query
# ======================


@pytest.mark.asyncio
async def test_delete_golden_query():
    driver = FakeDriver(
        [INSTALLED, ("DELETE FROM airman_semantic.golden_queries", rows({"name": "x"}))]
    )

    result = await delete_golden_query(driver, "monthly_revenue")

    assert result == "Deleted golden query 'monthly_revenue'"
    assert driver.last_params() == ["monthly_revenue"]


@pytest.mark.asyncio
async def test_delete_golden_query_not_found():
    driver = FakeDriver([INSTALLED, ("DELETE FROM airman_semantic.golden_queries", [])])

    with pytest.raises(ValueError, match="not found"):
        await delete_golden_query(driver, "missing")


@pytest.mark.asyncio
async def test_delete_golden_query_when_storage_not_installed():
    driver = FakeDriver([NOT_INSTALLED])

    with pytest.raises(ValueError, match="not found"):
        await delete_golden_query(driver, "missing")


# =========================
# search_golden_queries
# =========================


@pytest.mark.asyncio
async def test_search_golden_queries_basic(embedder):
    match = {
        "name": "customer_by_email",
        "description": "Find customer",
        "query_text": "SELECT * FROM customers WHERE email = ${email}",
        "parameters": [],
        "similarity": 0.85,
    }
    driver = FakeDriver(
        [INSTALLED, MAP_LOOKUP, ("airman_semantic.golden_queries a", rows(match))]
    )

    results = await search_golden_queries(
        driver, embedder, "my_map", "find customer", min_similarity=0.7
    )

    assert results == [match]
    vector, dims, model, min_sim, limit = driver.last_params()
    assert vector.startswith("[")
    assert (dims, model, min_sim, limit) == (64, "airman-hash-v1", 0.7, 10)


@pytest.mark.asyncio
async def test_search_golden_queries_map_not_found(embedder):
    driver = FakeDriver([INSTALLED, ("FROM airman_semantic.knowledge_maps", [])])

    with pytest.raises(ValueError, match="Knowledge map 'missing_map' not found"):
        await search_golden_queries(driver, embedder, "missing_map", "query")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"min_similarity": -0.5}, "min_similarity must be between"),
        ({"min_similarity": 1.5}, "min_similarity must be between"),
        ({"limit": 0}, "limit must be at least 1"),
        ({"limit": 1001}, "limit must not exceed 1000"),
        ({"query": ""}, "query must not be empty"),
    ],
)
@pytest.mark.asyncio
async def test_search_golden_queries_validates_inputs(embedder, kwargs, message):
    driver = FakeDriver([])
    args = {"map_name": "map", "query": "query"} | kwargs

    with pytest.raises(ValueError, match=message):
        await search_golden_queries(driver, embedder, **args)

    assert driver.calls == []


# ==========================
# execute_golden_query
# ==========================


@pytest.mark.asyncio
async def test_execute_golden_query_binds_values(raw_cursor):
    driver = FakeDriver(
        [INSTALLED, ("FROM airman_semantic.golden_queries", rows(EMAIL_QUERY))]
    )
    payload = "alice@example.com' OR '1'='1"

    results = await execute_golden_query(
        driver, "customer_by_email", parameters={"email": payload}
    )

    assert results == [{"id": 1, "email": "alice@example.com"}]
    assert driver.transactions == [True]  # READ ONLY transaction
    cursor = raw_cursor.instances[0]
    sql, values = cursor.execute.call_args.args
    assert sql == "SELECT * FROM customers WHERE email = ($1::text)"
    assert values == [payload]
    driver.connection.execute.assert_not_called()  # no role, no timeout


@pytest.mark.asyncio
async def test_execute_golden_query_without_params(raw_cursor):
    stored = {"query_text": "SELECT id FROM customers", "parameters": []}
    driver = FakeDriver(
        [INSTALLED, ("FROM airman_semantic.golden_queries", rows(stored))]
    )

    await execute_golden_query(driver, "all_customers")

    assert raw_cursor.instances[0].execute.call_args.args == (
        "SELECT id FROM customers",
        [],
    )


@pytest.mark.asyncio
async def test_execute_golden_query_with_role_uses_quoted_identifier(raw_cursor):
    driver = FakeDriver(
        [INSTALLED, ("FROM airman_semantic.golden_queries", rows(EMAIL_QUERY))]
    )

    await execute_golden_query(
        driver,
        "customer_by_email",
        parameters={"email": "a@b.c"},
        execute_as_role='reader"; RESET ROLE; --',
    )

    assert executed_sql(driver.connection) == [
        'SET LOCAL ROLE "reader""; RESET ROLE; --"'
    ]


@pytest.mark.parametrize("role", ["", "x" * 64, "a\x00b"])
@pytest.mark.asyncio
async def test_execute_golden_query_rejects_invalid_role(role):
    driver = FakeDriver([])

    with pytest.raises(ValueError, match="Invalid role name"):
        await execute_golden_query(driver, "query1", execute_as_role=role)

    assert driver.calls == []


@pytest.mark.asyncio
async def test_execute_golden_query_missing_parameter(raw_cursor):
    driver = FakeDriver(
        [INSTALLED, ("FROM airman_semantic.golden_queries", rows(EMAIL_QUERY))]
    )

    with pytest.raises(ValueError, match="Missing value for parameter 'email'"):
        await execute_golden_query(driver, "customer_by_email", parameters={})

    assert driver.transactions == []


@pytest.mark.asyncio
async def test_execute_golden_query_revalidates_stored_sql(raw_cursor):
    # A row tampered with directly in the database must not execute.
    tampered = {"query_text": "DELETE FROM customers", "parameters": []}
    driver = FakeDriver(
        [INSTALLED, ("FROM airman_semantic.golden_queries", rows(tampered))]
    )

    with pytest.raises(ValueError, match="must be a SELECT"):
        await execute_golden_query(driver, "tampered")

    assert driver.transactions == []


@pytest.mark.asyncio
async def test_execute_golden_query_not_found():
    driver = FakeDriver([INSTALLED, ("FROM airman_semantic.golden_queries", [])])

    with pytest.raises(ValueError, match="Golden query 'nope' not found"):
        await execute_golden_query(driver, "nope")


@pytest.mark.asyncio
async def test_execute_golden_query_empty_results(raw_cursor):
    raw_cursor.result_rows = []
    stored = {"query_text": "SELECT 1 WHERE false", "parameters": []}
    driver = FakeDriver(
        [INSTALLED, ("FROM airman_semantic.golden_queries", rows(stored))]
    )

    assert await execute_golden_query(driver, "empty_query") == []


# ==========================
# list_golden_queries
# ==========================


@pytest.mark.asyncio
async def test_list_golden_queries():
    driver = FakeDriver(
        [
            INSTALLED,
            (
                "jsonb_array_length(parameters) AS param_count",
                rows(
                    {"name": "customer_by_email", "param_count": 1},
                    {"name": "monthly_revenue", "param_count": 0},
                ),
            ),
        ]
    )

    results = await list_golden_queries(driver)

    assert [r["name"] for r in results] == ["customer_by_email", "monthly_revenue"]


@pytest.mark.asyncio
async def test_list_golden_queries_not_installed():
    driver = FakeDriver([NOT_INSTALLED])

    assert await list_golden_queries(driver) == []


# ============================================
# Restricted mode (SafeSqlDriver)
# ============================================


@pytest.fixture
def safe_sql_driver():
    """Create a SafeSqlDriver wrapping a fake SqlDriver."""
    base = FakeDriver([])
    driver = SafeSqlDriver(sql_driver=base, timeout=30)
    return driver, base


@pytest.mark.asyncio
async def test_execute_in_restricted_mode_sets_timeout_and_validates(
    safe_sql_driver, raw_cursor
):
    driver, base = safe_sql_driver
    base.routes = [
        INSTALLED,
        ("FROM airman_semantic.golden_queries", rows(EMAIL_QUERY)),
    ]

    await execute_golden_query(
        driver, "customer_by_email", {"email": "a@b.c"}, execute_as_role="reader"
    )

    assert base.transactions == [True]
    assert executed_sql(base.connection) == [
        "SET LOCAL statement_timeout = 30000",
        'SET LOCAL ROLE "reader"',
    ]


@pytest.mark.asyncio
async def test_execute_in_restricted_mode_applies_function_allowlist(
    safe_sql_driver, raw_cursor
):
    driver, base = safe_sql_driver
    stored = {"query_text": "SELECT pg_sleep(600)", "parameters": []}
    base.routes = [INSTALLED, ("FROM airman_semantic.golden_queries", rows(stored))]

    with pytest.raises(ValueError, match="Error validating query"):
        await execute_golden_query(driver, "sleepy")

    assert base.transactions == []


@pytest.mark.asyncio
async def test_search_golden_queries_through_safe_driver(safe_sql_driver, embedder):
    driver, base = safe_sql_driver
    base.routes = [INSTALLED, MAP_LOOKUP, ("airman_semantic.golden_queries a", [])]

    assert await search_golden_queries(driver, embedder, "my_map", "find") == []


@pytest.mark.asyncio
async def test_list_golden_queries_through_safe_driver(safe_sql_driver):
    driver, base = safe_sql_driver
    base.routes = [INSTALLED, ("param_count", rows({"name": "query1"}))]

    assert await list_golden_queries(driver) == [{"name": "query1"}]


@pytest.mark.asyncio
async def test_create_golden_query_blocked_in_restricted_by_default(
    safe_sql_driver, embedder
):
    driver, base = safe_sql_driver

    with patch(FLAG, False):
        with pytest.raises(ValueError, match="not permitted in restricted mode"):
            await create_golden_query(
                driver, embedder, "my_map", "test", "desc", "SELECT 1"
            )

    assert base.calls == []


@pytest.mark.asyncio
async def test_create_golden_query_allowed_in_restricted_with_flag(
    safe_sql_driver, embedder
):
    driver, base = safe_sql_driver
    base.routes = [
        INSTALLED,
        MAP_LOOKUP,
        ("INSERT INTO airman_semantic.golden_queries", rows({"name": "test"})),
    ]

    with patch(FLAG, True):
        result = await create_golden_query(
            driver, embedder, "my_map", "test", "desc", "SELECT 1"
        )

    assert result == "Created golden query 'test'"


@pytest.mark.asyncio
async def test_create_golden_query_in_restricted_mode_rejects_disallowed_functions(
    safe_sql_driver, embedder
):
    driver, base = safe_sql_driver

    with patch(FLAG, True):
        with pytest.raises(ValueError, match="Error validating query"):
            await create_golden_query(
                driver, embedder, "my_map", "test", "desc", "SELECT pg_sleep(600)"
            )

    assert base.calls == []


@pytest.mark.asyncio
async def test_delete_golden_query_blocked_in_restricted_by_default(safe_sql_driver):
    driver, base = safe_sql_driver

    with patch(FLAG, False):
        with pytest.raises(ValueError, match="not permitted"):
            await delete_golden_query(driver, "test")

    assert base.calls == []


@pytest.mark.asyncio
async def test_delete_golden_query_allowed_in_restricted_with_flag(safe_sql_driver):
    driver, base = safe_sql_driver
    base.routes = [
        INSTALLED,
        ("DELETE FROM airman_semantic.golden_queries", rows({"name": "test"})),
    ]

    with patch(FLAG, True):
        result = await delete_golden_query(driver, "test")

    assert result == "Deleted golden query 'test'"
