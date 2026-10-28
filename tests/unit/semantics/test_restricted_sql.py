# pyright: reportPrivateUsage=false
"""Every query the semantic tools run in restricted mode must pass SafeSqlDriver.

These tests parse the real SQL constants with SafeSqlDriver's validator, so a
change that introduces a non-allowlisted function or statement type fails
here instead of only at runtime in restricted mode.
"""

import pytest

from pg_airman_mcp.semantics import catalog, golden_query_tools, knowledge_map_tools
from pg_airman_mcp.sql import SafeSqlDriver, SqlDriver

READ_QUERIES = {
    "knowledge_map search": knowledge_map_tools._SEARCH_SQL,
    "knowledge_map stats": knowledge_map_tools._STATS_SQL,
    "knowledge_map stored hashes": knowledge_map_tools._STORED_HASHES_SQL,
    "golden query search": golden_query_tools._SEARCH_SQL,
    "catalog relations": catalog._RELATIONS_SQL,
    "catalog columns": catalog._COLUMNS_SQL,
    "catalog constraints": catalog._CONSTRAINTS_SQL,
    "installed check": "SELECT to_regclass(%s) IS NOT NULL AS installed",
}


@pytest.fixture
def validator():
    return SafeSqlDriver(
        sql_driver=SqlDriver(engine_url="postgresql://unused"), timeout=None
    )


@pytest.mark.parametrize("name", sorted(READ_QUERIES))
def test_read_queries_pass_restricted_mode_validation(validator, name):
    validator.validate_sql(READ_QUERIES[name])


@pytest.mark.parametrize(
    "query",
    [
        knowledge_map_tools._UPSERT_ENTITIES_SQL,
        knowledge_map_tools._DELETE_ENTITIES_SQL,
    ],
)
def test_write_queries_are_rejected_by_restricted_mode(validator, query):
    with pytest.raises(ValueError):
        validator.validate_sql(query)


def test_aidb_functions_are_no_longer_allowlisted():
    assert not any(f.startswith("aidb.") for f in SafeSqlDriver.ALLOWED_FUNCTIONS)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT vector_dims(embedding) FROM items",
        "SELECT cosine_distance(a, b), l2_distance(a, b), inner_product(a, b) FROM t",
        "SELECT id FROM items ORDER BY embedding <=> '[1,2,3]' LIMIT 5",
        "SELECT l2_normalize(embedding) FROM items",
    ],
)
def test_pgvector_read_functions_allowed(validator, query):
    validator.validate_sql(query)


def test_safe_driver_does_not_hand_out_transactions(validator):
    with pytest.raises(ValueError, match="does not provide transactions"):
        validator.transaction(readonly=True)
