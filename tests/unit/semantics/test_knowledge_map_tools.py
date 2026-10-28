"""Tests for knowledge_map_tools module (pgvector backend)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fake_driver import FakeDriver, rows

from pgpacer.semantics.embeddings import (
    HashEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
)
from pgpacer.semantics.knowledge_map_tools import (
    create_knowledge_map,
    delete_knowledge_map,
    get_knowledge_map_stats,
    list_knowledge_maps,
    refresh_knowledge_map,
    search_knowledge_map,
)
from pgpacer.sql import SafeSqlDriver

MAP_ROW = {
    "name": "test_map",
    "model_name": "pgpacer-hash-v1",
    "dimensions": 64,
    "schemas": ["sales"],
    "refreshed_at": datetime(2026, 1, 1, tzinfo=UTC),
}
RELATION = {
    "schema_name": "sales",
    "relation_name": "customers",
    "relkind": "r",
    "comment": "Customer master data",
}
COLUMN = {
    "schema_name": "sales",
    "relation_name": "customers",
    "column_name": "email",
    "data_type": "text",
    "not_null": True,
    "comment": "Customer email",
}


INSTALLED = ("to_regclass", rows({"installed": True}))
NOT_INSTALLED = ("to_regclass", rows({"installed": False}))
MAP_LOOKUP = ("FROM pgpacer.knowledge_maps\n        WHERE name", rows(MAP_ROW))
CATALOG = [
    ("FROM pg_catalog.pg_class c", rows(RELATION)),
    ("FROM pg_catalog.pg_attribute", rows(COLUMN)),
    ("FROM pg_catalog.pg_constraint", []),
]


@pytest.fixture
def embedder():
    return HashEmbeddingProvider(dimensions=64)


# ======================
# search_knowledge_map
# ======================


@pytest.mark.asyncio
async def test_search_knowledge_map_basic(embedder):
    result_row = {
        "schema_name": "sales",
        "relation_name": "customers",
        "column_name": None,
        "entity_type": "Table",
        "definition": "Table sales.customers",
        "comment": "Customer master data",
        "similarity": 0.92,
    }
    driver = FakeDriver(
        [
            INSTALLED,
            MAP_LOOKUP,
            ("pgpacer.knowledge_map_entries e", rows(result_row)),
        ]
    )

    results = await search_knowledge_map(
        driver, embedder, "test_map", "customer email", min_similarity=0.7, limit=5
    )

    assert results == [result_row]
    vector_literal, dims, map_name, types, min_sim, limit = driver.last_params()
    assert vector_literal.startswith("[") and vector_literal.endswith("]")
    assert dims == 64
    assert map_name == "test_map"
    assert types == ["Column", "Table", "View"]
    assert (min_sim, limit) == (0.7, 5)


@pytest.mark.asyncio
async def test_search_knowledge_map_entity_filter_is_parameterized(embedder):
    driver = FakeDriver(
        [INSTALLED, MAP_LOOKUP, ("pgpacer.knowledge_map_entries e", [])]
    )

    await search_knowledge_map(
        driver,
        embedder,
        "test_map",
        "customer",
        entity_types=["table", "VIEW", "Table"],
    )

    sql = driver.calls[-1][0]
    assert driver.last_params()[3] == ["Table", "View"]
    # The filter is a bind parameter, not interpolated SQL.
    assert "'Table'" not in sql
    assert "ANY(%s::text[])" in sql


@pytest.mark.asyncio
async def test_search_knowledge_map_invalid_entity_type(embedder):
    driver = FakeDriver([])

    with pytest.raises(ValueError, match="Invalid entity types"):
        await search_knowledge_map(
            driver,
            embedder,
            "test_map",
            "customer",
            entity_types=["Table", "InvalidType'; DROP TABLE users; --"],
        )

    assert driver.calls == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"min_similarity": -0.5}, "min_similarity must be between"),
        ({"min_similarity": 1.5}, "min_similarity must be between"),
        ({"min_similarity": float("nan")}, "min_similarity must be between"),
        ({"limit": 0}, "limit must be at least 1"),
        ({"limit": -5}, "limit must be at least 1"),
        ({"limit": 1001}, "limit must not exceed 1000"),
        ({"query": "   "}, "query must not be empty"),
        ({"query": "x" * 2001}, "must not exceed"),
        ({"map_name": "bad name; --"}, "Invalid knowledge map name"),
    ],
)
@pytest.mark.asyncio
async def test_search_knowledge_map_validates_inputs(embedder, kwargs, message):
    driver = FakeDriver([])
    args = {"map_name": "test_map", "query": "customer"} | kwargs

    with pytest.raises(ValueError, match=message):
        await search_knowledge_map(driver, embedder, **args)

    assert driver.calls == []


@pytest.mark.asyncio
async def test_search_knowledge_map_unknown_map(embedder):
    driver = FakeDriver([INSTALLED, ("FROM pgpacer.knowledge_maps", [])])

    with pytest.raises(ValueError, match="Knowledge map 'missing_map' not found"):
        await search_knowledge_map(driver, embedder, "missing_map", "query")


@pytest.mark.asyncio
async def test_search_knowledge_map_when_storage_not_installed(embedder):
    driver = FakeDriver([NOT_INSTALLED])

    with pytest.raises(ValueError, match="not found"):
        await search_knowledge_map(driver, embedder, "test_map", "query")


@pytest.mark.asyncio
async def test_search_knowledge_map_rejects_model_mismatch():
    driver = FakeDriver([INSTALLED, MAP_LOOKUP])
    other = OpenAICompatibleEmbeddingProvider(
        model="text-embedding-3-small", base_url="https://api.example.com/v1"
    )

    with patch.object(other, "_embed_batch", AsyncMock()) as embed_batch:
        with pytest.raises(ValueError, match="not comparable"):
            await search_knowledge_map(driver, other, "test_map", "customer")

    embed_batch.assert_not_called()


@pytest.mark.asyncio
async def test_search_knowledge_map_rejects_dimension_mismatch():
    driver = FakeDriver([INSTALLED, MAP_LOOKUP])

    with pytest.raises(ValueError, match="64-dimensional"):
        await search_knowledge_map(
            driver, HashEmbeddingProvider(dimensions=128), "test_map", "customer"
        )


# ======================
# list_knowledge_maps
# ======================


@pytest.mark.asyncio
async def test_list_knowledge_maps():
    created = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    driver = FakeDriver(
        [
            INSTALLED,
            (
                "'Disabled' AS auto_processing",
                rows(
                    {
                        "name": "analytics_map",
                        "model_name": "pgpacer-hash-v1",
                        "dimensions": 512,
                        "schemas": ["public", "sales"],
                        "auto_processing": "Disabled",
                        "created_at": created,
                        "refreshed_at": None,
                    }
                ),
            ),
        ]
    )

    results = await list_knowledge_maps(driver)

    assert len(results) == 1
    assert results[0]["name"] == "analytics_map"
    assert results[0]["schemas"] == ["public", "sales"]
    assert results[0]["created_at"] == created.isoformat()


@pytest.mark.asyncio
async def test_list_knowledge_maps_not_installed():
    driver = FakeDriver([NOT_INSTALLED])

    assert await list_knowledge_maps(driver) == []
    assert len(driver.calls) == 1


# ======================
# get_knowledge_map_stats
# ======================


@pytest.mark.asyncio
async def test_get_knowledge_map_stats_reports_pending_changes():
    # The catalog has a table and a column; only the table is stored, with a
    # stale hash -> 1 update + 1 addition pending.
    driver = FakeDriver(
        [
            INSTALLED,
            MAP_LOOKUP,
            (
                "count(*) AS total",
                rows({"total": 1, "tables": 1, "views": 0, "columns": 0}),
            ),
            *CATALOG,
            (
                "SELECT schema_name, relation_name, column_name, content_hash",
                rows(
                    {
                        "schema_name": "sales",
                        "relation_name": "customers",
                        "column_name": "",
                        "content_hash": "stale",
                    }
                ),
            ),
        ]
    )

    result = await get_knowledge_map_stats(driver, "test_map")

    assert result["total"] == 1
    assert result["tables"] == 1
    assert result["pending"] == 2
    assert result["model_name"] == "pgpacer-hash-v1"
    assert result["refreshed_at"] == MAP_ROW["refreshed_at"].isoformat()


@pytest.mark.asyncio
async def test_get_knowledge_map_stats_not_found():
    driver = FakeDriver([INSTALLED, ("FROM pgpacer.knowledge_maps", [])])

    with pytest.raises(ValueError, match="not found"):
        await get_knowledge_map_stats(driver, "nonexistent_map")


# ======================
# Write operations
# ======================


@pytest.fixture
def safe_driver():
    base = FakeDriver([])
    return SafeSqlDriver(sql_driver=base, timeout=None), base


@pytest.mark.asyncio
async def test_refresh_rejected_in_restricted_mode(safe_driver, embedder):
    driver, base = safe_driver

    with pytest.raises(ValueError, match="not permitted in restricted mode"):
        await refresh_knowledge_map(driver, embedder, "test_map")

    assert base.calls == []


@pytest.mark.asyncio
async def test_create_rejected_in_restricted_mode(safe_driver, embedder):
    driver, base = safe_driver

    with pytest.raises(ValueError, match="not permitted in restricted mode"):
        await create_knowledge_map(driver, embedder, "test_map", ["sales"])

    assert base.calls == []


@pytest.mark.asyncio
async def test_delete_rejected_in_restricted_mode(safe_driver):
    driver, base = safe_driver

    with pytest.raises(ValueError, match="not permitted in restricted mode"):
        await delete_knowledge_map(driver, "test_map")

    assert base.calls == []


@pytest.mark.asyncio
async def test_refresh_knowledge_map_applies_changes_in_one_transaction(embedder):
    driver = FakeDriver(
        [
            INSTALLED,
            MAP_LOOKUP,
            *CATALOG,
            (
                "SELECT schema_name, relation_name, column_name, content_hash",
                rows(
                    {
                        "schema_name": "sales",
                        "relation_name": "dropped",
                        "column_name": "",
                        "content_hash": "x",
                    }
                ),
            ),
        ],
        fetchone_results=[(1,)],
    )

    result = await refresh_knowledge_map(driver, embedder, "test_map")

    assert result == (
        "Refreshed knowledge map 'test_map': 2 added, 0 updated, 1 removed"
    )
    assert driver.transactions == [False]
    statements = [c.args[0] for c in driver.cursor.execute.call_args_list]
    assert "FOR UPDATE" in statements[0]
    assert "DELETE FROM pgpacer.knowledge_map_entries" in statements[1]
    assert "INSERT INTO pgpacer.knowledge_map_entries" in statements[2]
    assert "refreshed_at = now()" in statements[3]
    upsert_params = driver.cursor.execute.call_args_list[2].args[1]
    assert upsert_params[0] == "test_map"
    assert upsert_params[3] == ["", "email"]  # '' marks relation rows


@pytest.mark.asyncio
async def test_refresh_knowledge_map_rejects_model_mismatch():
    driver = FakeDriver([INSTALLED, MAP_LOOKUP])

    with pytest.raises(ValueError, match="64-dimensional"):
        await refresh_knowledge_map(
            driver, HashEmbeddingProvider(dimensions=32), "test_map"
        )

    assert driver.transactions == []


@pytest.mark.parametrize(
    ("schemas", "message"),
    [
        ([], "non-empty list"),
        (["pg_catalog"], "System schema"),
        (["information_schema"], "System schema"),
        (["pgpacer"], "System schema"),
        (["x" * 64], "Invalid schema name"),
        ([""], "Invalid schema name"),
        ([None], "Invalid schema name"),
    ],
)
@pytest.mark.asyncio
async def test_create_knowledge_map_validates_schemas(embedder, schemas, message):
    driver = FakeDriver([])

    with pytest.raises(ValueError, match=message):
        await create_knowledge_map(driver, embedder, "test_map", schemas)

    assert driver.calls == []


@pytest.mark.asyncio
async def test_create_knowledge_map_reports_missing_schemas(embedder):
    driver = FakeDriver(
        [INSTALLED, ("SELECT nspname FROM", rows({"nspname": "sales"}))]
    )

    with pytest.raises(ValueError, match="Schema\\(s\\) not found: nope"):
        await create_knowledge_map(driver, embedder, "test_map", ["sales", "nope"])


@pytest.mark.asyncio
async def test_create_knowledge_map_rejects_duplicates(embedder):
    driver = FakeDriver(
        [
            INSTALLED,
            ("SELECT nspname FROM", rows({"nspname": "sales"})),
            ("SELECT 1 FROM pgpacer.knowledge_maps", rows({"?column?": 1})),
        ]
    )

    with pytest.raises(ValueError, match="already exists"):
        await create_knowledge_map(driver, embedder, "test_map", ["sales"])

    assert driver.transactions == []


@pytest.mark.asyncio
async def test_create_knowledge_map_stores_map_and_entities(embedder):
    driver = FakeDriver(
        [
            INSTALLED,
            ("SELECT nspname FROM", rows({"nspname": "sales"})),
            ("SELECT 1 FROM pgpacer.knowledge_maps", []),
            *CATALOG,
        ],
        fetchone_results=[("test_map",)],
    )

    result = await create_knowledge_map(
        driver, embedder, "test_map", ["sales", "sales"]
    )

    assert result == (
        "Created knowledge map 'test_map' with 2 entities "
        "(1 tables, 0 views, 1 columns) using model 'pgpacer-hash-v1'"
    )
    insert_map = driver.cursor.execute.call_args_list[0].args
    assert "INSERT INTO pgpacer.knowledge_maps" in insert_map[0]
    assert insert_map[1] == ["test_map", "pgpacer-hash-v1", 64, ["sales"]]
    upsert = driver.cursor.execute.call_args_list[1].args
    assert "INSERT INTO pgpacer.knowledge_map_entries" in upsert[0]
    assert len(upsert[1][-1]) == 2  # two embeddings


@pytest.mark.asyncio
async def test_create_knowledge_map_requires_pgvector(embedder):
    driver = FakeDriver([NOT_INSTALLED], fetchone_results=[None])
    driver.connection.execute = AsyncMock(
        return_value=MagicMock(fetchone=AsyncMock(return_value=None))
    )

    with pytest.raises(ValueError, match="pgvector extension is not installed"):
        await create_knowledge_map(driver, embedder, "test_map", ["sales"])


@pytest.mark.asyncio
async def test_delete_knowledge_map():
    driver = FakeDriver(
        [
            INSTALLED,
            ("DELETE FROM pgpacer.knowledge_maps", rows({"name": "k"})),
        ]
    )

    assert await delete_knowledge_map(driver, "test_map") == (
        "Deleted knowledge map 'test_map'"
    )
    assert driver.last_params() == ["test_map"]


@pytest.mark.asyncio
async def test_delete_knowledge_map_not_found():
    driver = FakeDriver([INSTALLED, ("DELETE FROM pgpacer.knowledge_maps", [])])

    with pytest.raises(ValueError, match="not found"):
        await delete_knowledge_map(driver, "test_map")
