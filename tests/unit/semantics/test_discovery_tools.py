"""Tests for discovery_tools module."""

from unittest.mock import AsyncMock

import pytest
from fake_driver import FakeDriver, rows

from pgpacer.semantics.discovery_tools import discover_schema_for_question
from pgpacer.semantics.embeddings import HashEmbeddingProvider

MAP_ROW = {
    "name": "my_map",
    "model_name": "pgpacer-hash-v1",
    "dimensions": 64,
    "schemas": ["public"],
    "refreshed_at": None,
}
TABLE_MATCH = {
    "schema_name": "public",
    "relation_name": "orders",
    "column_name": None,
    "entity_type": "Table",
    "definition": "Table public.orders",
    "comment": "Customer orders table",
    "similarity": 0.85,
}
GOLDEN_MATCH = {
    "name": "monthly_revenue",
    "description": "Revenue per month",
    "query_text": "SELECT SUM(total) FROM orders GROUP BY month",
    "parameters": [],
    "similarity": 0.78,
}


def routing_driver(map_matches, golden_query_matches) -> FakeDriver:
    return FakeDriver(
        [
            ("to_regclass", rows({"installed": True})),
            ("FROM pgpacer.knowledge_maps", rows(MAP_ROW)),
            ("pgpacer.knowledge_map_entries e", rows(*map_matches)),
            ("pgpacer.golden_queries a", rows(*golden_query_matches)),
        ]
    )


@pytest.fixture
def embedder():
    provider = HashEmbeddingProvider(dimensions=64)
    provider.embed = AsyncMock(wraps=provider.embed)
    return provider


@pytest.mark.asyncio
async def test_discover_returns_both_metadata_and_golden_queries(embedder):
    driver = routing_driver([TABLE_MATCH], [GOLDEN_MATCH])

    results = await discover_schema_for_question(
        driver, embedder, map_name="my_map", question="how much revenue per month"
    )

    assert results == {
        "schema_metadata": [TABLE_MATCH],
        "golden_queries": [GOLDEN_MATCH],
    }
    # The question is embedded once and reused for both searches.
    assert embedder.embed.await_count == 1
    map_vector = driver.params_matching("knowledge_map_entries e")[0][0]
    golden_query_vector = driver.params_matching("golden_queries a")[0][0]
    assert map_vector == golden_query_vector


@pytest.mark.asyncio
async def test_discover_without_golden_queries(embedder):
    driver = routing_driver([TABLE_MATCH], [GOLDEN_MATCH])

    results = await discover_schema_for_question(
        driver,
        embedder,
        map_name="my_map",
        question="find customers",
        include_golden_queries=False,
    )

    assert results["schema_metadata"] == [TABLE_MATCH]
    assert results["golden_queries"] == []
    assert driver.params_matching("golden_queries a") == []


@pytest.mark.asyncio
async def test_discover_empty_results(embedder):
    driver = routing_driver([], [])

    results = await discover_schema_for_question(
        driver, embedder, map_name="my_map", question="quantum physics equations"
    )

    assert results == {"schema_metadata": [], "golden_queries": []}


@pytest.mark.asyncio
async def test_discover_passes_similarity_and_limit(embedder):
    driver = routing_driver([], [])

    await discover_schema_for_question(
        driver,
        embedder,
        map_name="my_map",
        question="test",
        min_similarity=0.3,
        max_results=5,
    )

    map_params = driver.params_matching("knowledge_map_entries e")[0]
    assert map_params[-2:] == [0.3, 5]
    golden_query_params = driver.params_matching("golden_queries a")[0]
    assert golden_query_params[-2:] == [0.3, 5]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"min_similarity": 2.0}, "min_similarity"),
        ({"max_results": 0}, "limit must be at least 1"),
        ({"question": " "}, "query must not be empty"),
    ],
)
@pytest.mark.asyncio
async def test_discover_validates_inputs(embedder, kwargs, message):
    driver = routing_driver([], [])
    args = {"map_name": "my_map", "question": "test"} | kwargs

    with pytest.raises(ValueError, match=message):
        await discover_schema_for_question(driver, embedder, **args)

    assert driver.calls == []


@pytest.mark.asyncio
async def test_discover_propagates_map_error(embedder):
    driver = FakeDriver(error=Exception("DB connection failed"))

    with pytest.raises(Exception, match="DB connection failed"):
        await discover_schema_for_question(
            driver, embedder, map_name="my_map", question="test"
        )
