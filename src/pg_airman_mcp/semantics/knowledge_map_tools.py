"""Semantic Knowledge Base tools backed by pgvector.

A knowledge map stores an embedding of every table, view and column in a set
of schemas (``airman_semantic.knowledge_map_entries``) so schema elements can be found
by natural-language similarity search. Embeddings come from the server's
configured EmbeddingProvider; refreshes re-embed only entities whose
description changed.
"""

import logging
from collections.abc import Sequence
from typing import Any

from pg_airman_mcp.sql import SafeSqlDriver, SqlDriver

from .catalog import (
    CatalogEntity,
    EntityKey,
    SyncPlan,
    plan_sync,
    read_catalog_entities,
)
from .embeddings import EmbeddingProvider
from .store import (
    SEMANTIC_SCHEMA,
    KnowledgeMap,
    check_dimensions,
    check_model,
    ensure_installed,
    get_knowledge_map,
    is_installed,
    jsonable,
    require_unrestricted,
    to_vector_literal,
    validate_limit,
    validate_name,
    validate_query,
    validate_similarity,
)

logger = logging.getLogger(__name__)

# Valid entity types for knowledge map search
VALID_ENTITY_TYPES = frozenset({"Table", "View", "Column"})
_ENTITY_TYPE_LOOKUP = {t.lower(): t for t in VALID_ENTITY_TYPES}

MAX_MAP_SCHEMAS = 64
MAX_MAP_ENTRIES = 50_000
_WRITE_CHUNK_SIZE = 500

# The CASE guarantees vector_dims() is checked before <=> is evaluated, so a
# row with mismatched dimensions is skipped instead of aborting the query.
_SEARCH_SQL = """
    WITH q AS (SELECT %s::vector AS embedding, %s::integer AS dims)
    SELECT s.schema_name, s.relation_name, s.column_name, s.entity_type,
           s.definition, s.comment, s.similarity
    FROM (
        SELECT e.schema_name,
               e.relation_name,
               NULLIF(e.column_name, '') AS column_name,
               e.entity_type,
               e.definition,
               e.comment,
               CASE WHEN vector_dims(e.embedding) = q.dims
                    THEN 1 - (e.embedding <=> q.embedding)
               END AS similarity
        FROM airman_semantic.knowledge_map_entries e, q
        WHERE e.map_name = %s
          AND e.entity_type = ANY(%s::text[])
    ) s
    WHERE s.similarity >= %s
    ORDER BY s.similarity DESC, s.schema_name, s.relation_name, s.column_name
    LIMIT %s
"""

_STATS_SQL = """
    SELECT count(*) AS total,
           count(*) FILTER (WHERE entity_type = 'Table') AS tables,
           count(*) FILTER (WHERE entity_type = 'View') AS views,
           count(*) FILTER (WHERE entity_type = 'Column') AS columns
    FROM airman_semantic.knowledge_map_entries
    WHERE map_name = %s
"""

_STORED_HASHES_SQL = """
    SELECT schema_name, relation_name, column_name, content_hash
    FROM airman_semantic.knowledge_map_entries
    WHERE map_name = %s
"""

_DELETE_ENTITIES_SQL = """
    DELETE FROM airman_semantic.knowledge_map_entries e
    USING unnest(%s::text[], %s::text[], %s::text[])
          AS d(schema_name, relation_name, column_name)
    WHERE e.map_name = %s
      AND e.schema_name = d.schema_name
      AND e.relation_name = d.relation_name
      AND e.column_name = d.column_name
"""

_UPSERT_ENTITIES_SQL = """
    INSERT INTO airman_semantic.knowledge_map_entries AS e
        (map_name, schema_name, relation_name, column_name, entity_type,
         definition, comment, content_hash, embedding)
    SELECT %s::text, u.schema_name, u.relation_name, u.column_name, u.entity_type,
           u.definition, u.comment, u.content_hash, u.embedding::vector
    FROM unnest(%s::text[], %s::text[], %s::text[], %s::text[],
                %s::text[], %s::text[], %s::text[], %s::text[])
         AS u(schema_name, relation_name, column_name, entity_type,
              definition, comment, content_hash, embedding)
    ON CONFLICT (map_name, schema_name, relation_name, column_name) DO UPDATE
    SET entity_type = EXCLUDED.entity_type,
        definition = EXCLUDED.definition,
        comment = EXCLUDED.comment,
        content_hash = EXCLUDED.content_hash,
        embedding = EXCLUDED.embedding,
        updated_at = now()
"""


async def search_knowledge_map(
    sql_driver: SqlDriver | SafeSqlDriver,
    embedder: EmbeddingProvider,
    map_name: str,
    query: str,
    min_similarity: float = 0.7,
    entity_types: list[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search the knowledge map for relevant tables/columns.

    Args:
        sql_driver: SqlDriver or SafeSqlDriver instance.
        embedder: Embedding provider used to embed the query.
        map_name: Name of the knowledge map to search.
        query: Natural language query describing what you're looking for.
        min_similarity: Minimum cosine similarity threshold (0.0-1.0).
        entity_types: Optional filter by entity types: 'Table', 'View', 'Column'.
        limit: Maximum number of results to return (1-1000).

    Returns:
        List of matching entities with schema, relation, column info and similarity.
    """
    validate_similarity(min_similarity)
    validate_limit(limit)
    validate_query(query)
    types = _normalize_entity_types(entity_types)

    knowledge_map = await get_knowledge_map(sql_driver, map_name)
    check_model(
        f"Knowledge map '{knowledge_map.name}'",
        knowledge_map.model_name,
        knowledge_map.dimensions,
        embedder,
    )
    vector = await embedder.embed_one(query)
    return await search_knowledge_map_entries(
        sql_driver, knowledge_map, vector, min_similarity, types, limit
    )


async def search_knowledge_map_entries(
    sql_driver: SqlDriver | SafeSqlDriver,
    knowledge_map: KnowledgeMap,
    vector: Sequence[float],
    min_similarity: float,
    entity_types: Sequence[str],
    limit: int,
) -> list[dict[str, Any]]:
    """Run a similarity search with an already-computed query embedding."""
    check_dimensions(
        f"Knowledge map '{knowledge_map.name}'", vector, knowledge_map.dimensions
    )
    rows = await sql_driver.execute_query(
        _SEARCH_SQL,
        [
            to_vector_literal(vector),
            knowledge_map.dimensions,
            knowledge_map.name,
            list(entity_types),
            min_similarity,
            limit,
        ],
    )
    results = [row.cells for row in (rows or [])]
    logger.info(
        f"Knowledge map search in '{knowledge_map.name}' "
        f"returned {len(results)} results"
    )
    return results


async def list_knowledge_maps(
    sql_driver: SqlDriver | SafeSqlDriver,
) -> list[dict[str, Any]]:
    """List all available knowledge maps.

    Args:
        sql_driver: SqlDriver or SafeSqlDriver instance.

    Returns:
        List of knowledge maps with name, model_name, schemas, and auto_processing info.
    """
    if not await is_installed(sql_driver):
        return []
    # auto_processing is kept for AIDB compatibility: pgvector knowledge maps
    # are only synchronised by refresh_knowledge_map, never automatically.
    rows = await sql_driver.execute_query(
        """
        SELECT name, model_name, dimensions, schemas,
               'Disabled' AS auto_processing, created_at, refreshed_at
        FROM airman_semantic.knowledge_maps
        ORDER BY name
        """
    )
    results = [jsonable(row.cells) for row in (rows or [])]
    logger.info(f"Found {len(results)} knowledge maps")
    return results


async def get_knowledge_map_stats(
    sql_driver: SqlDriver | SafeSqlDriver,
    map_name: str,
) -> dict[str, Any]:
    """Get statistics about a knowledge map.

    ``pending`` is the number of entities a refresh would add, update or
    remove, computed by comparing the stored entities with the live catalog.

    Args:
        sql_driver: SqlDriver or SafeSqlDriver instance.
        map_name: Name of the knowledge map.

    Returns:
        Dict with total, tables, views, columns, and pending counts.
    """
    knowledge_map = await get_knowledge_map(sql_driver, map_name)
    rows = await sql_driver.execute_query(_STATS_SQL, [knowledge_map.name])
    result: dict[str, Any] = dict(rows[0].cells) if rows else {}
    plan = await _plan_sync(sql_driver, knowledge_map)
    result["pending"] = plan.pending
    result["model_name"] = knowledge_map.model_name
    result["dimensions"] = knowledge_map.dimensions
    result["refreshed_at"] = knowledge_map.refreshed_at
    result = jsonable(result)
    logger.info(
        f"Knowledge map '{knowledge_map.name}' stats: "
        f"{result.get('total', 0)} entities, "
        f"{result['pending']} pending"
    )
    return result


async def refresh_knowledge_map(
    sql_driver: SqlDriver | SafeSqlDriver,
    embedder: EmbeddingProvider,
    map_name: str,
) -> str:
    """Refresh the knowledge map to sync with current schema metadata.

    Only new or changed entities are re-embedded; dropped objects are removed.
    All changes are applied in a single transaction.

    Args:
        sql_driver: SqlDriver instance (not permitted in restricted mode).
        embedder: Embedding provider matching the knowledge map's model.
        map_name: Name of the knowledge map to refresh.

    Returns:
        Success message with change counts.
    """
    driver = require_unrestricted(sql_driver, "Refreshing a knowledge map")
    knowledge_map = await get_knowledge_map(driver, map_name)
    check_model(
        f"Knowledge map '{knowledge_map.name}'",
        knowledge_map.model_name,
        knowledge_map.dimensions,
        embedder,
    )
    plan = await _plan_sync(driver, knowledge_map)
    vectors = await _embed_entities(embedder, plan.to_upsert, knowledge_map)

    async with driver.transaction() as conn:
        async with conn.cursor() as cur:
            # Lock the knowledge map row so concurrent refreshes/deletes serialise.
            await cur.execute(
                "SELECT 1 FROM airman_semantic.knowledge_maps WHERE name = %s "
                "FOR UPDATE",
                [knowledge_map.name],
            )
            if await cur.fetchone() is None:
                raise ValueError(f"Knowledge map '{knowledge_map.name}' not found")
            await _apply_plan(cur, knowledge_map.name, plan, vectors)
            await cur.execute(
                "UPDATE airman_semantic.knowledge_maps SET refreshed_at = now() "
                "WHERE name = %s",
                [knowledge_map.name],
            )

    message = (
        f"Refreshed knowledge map '{knowledge_map.name}': {plan.added} added, "
        f"{plan.updated} updated, {len(plan.to_delete)} removed"
    )
    logger.info(message)
    return message


async def create_knowledge_map(
    sql_driver: SqlDriver | SafeSqlDriver,
    embedder: EmbeddingProvider,
    map_name: str,
    schemas: Sequence[str],
) -> str:
    """Create a knowledge map over the given schemas.

    Installs the ``airman_semantic`` storage schema on first use, embeds every
    table, view and column, and stores the knowledge map atomically.

    Args:
        sql_driver: SqlDriver instance (not permitted in restricted mode).
        embedder: Embedding provider; its model is recorded on the knowledge map.
        map_name: Unique knowledge map name.
        schemas: Database schemas to include.

    Returns:
        Success message with entity counts.
    """
    driver = require_unrestricted(sql_driver, "Creating a knowledge map")
    validate_name("knowledge map", map_name)
    schema_list = _validate_schemas(schemas)

    await ensure_installed(driver)
    rows = await driver.execute_query(
        "SELECT nspname FROM pg_catalog.pg_namespace WHERE nspname = ANY(%s::text[])",
        [schema_list],
    )
    found = {row.cells["nspname"] for row in (rows or [])}
    missing = [s for s in schema_list if s not in found]
    if missing:
        raise ValueError(f"Schema(s) not found: {', '.join(missing)}")
    # Fail fast before spending embedding calls; the INSERT below is the
    # authoritative uniqueness check.
    if await driver.execute_query(
        "SELECT 1 FROM airman_semantic.knowledge_maps WHERE name = %s", [map_name]
    ):
        raise ValueError(f"Knowledge map '{map_name}' already exists")

    entities = await read_catalog_entities(driver, schema_list)
    _check_entity_count(len(entities))
    plan = plan_sync(entities, {})
    dimensions = await embedder.resolve_dimensions()
    knowledge_map = KnowledgeMap(
        map_name, embedder.model_name, dimensions, tuple(schema_list)
    )
    vectors = await _embed_entities(embedder, plan.to_upsert, knowledge_map)

    async with driver.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO airman_semantic.knowledge_maps
                    (name, model_name, dimensions, schemas, refreshed_at)
                VALUES (%s, %s, %s, %s::text[], now())
                ON CONFLICT (name) DO NOTHING
                RETURNING name
                """,
                [
                    knowledge_map.name,
                    knowledge_map.model_name,
                    knowledge_map.dimensions,
                    list(knowledge_map.schemas),
                ],
            )
            if await cur.fetchone() is None:
                raise ValueError(f"Knowledge map '{map_name}' already exists")
            await _apply_plan(cur, knowledge_map.name, plan, vectors)

    counts = {
        t: sum(1 for e in entities if e.entity_type == t) for t in VALID_ENTITY_TYPES
    }
    message = (
        f"Created knowledge map '{map_name}' with {len(entities)} entities "
        f"({counts['Table']} tables, {counts['View']} views, "
        f"{counts['Column']} columns) using model '{knowledge_map.model_name}'"
    )
    logger.info(message)
    return message


async def delete_knowledge_map(
    sql_driver: SqlDriver | SafeSqlDriver,
    map_name: str,
) -> str:
    """Delete a knowledge map and all of its stored embeddings.

    Args:
        sql_driver: SqlDriver instance (not permitted in restricted mode).
        map_name: Name of the knowledge map to delete.

    Returns:
        Success message.
    """
    driver = require_unrestricted(sql_driver, "Deleting a knowledge map")
    validate_name("knowledge map", map_name)
    if not await is_installed(driver):
        raise ValueError(f"Knowledge map '{map_name}' not found")
    rows = await driver.execute_query(
        "DELETE FROM airman_semantic.knowledge_maps WHERE name = %s RETURNING name",
        [map_name],
    )
    if not rows:
        raise ValueError(f"Knowledge map '{map_name}' not found")
    logger.info(f"Deleted knowledge map '{map_name}'")
    return f"Deleted knowledge map '{map_name}'"


def _normalize_entity_types(entity_types: list[str] | None) -> list[str]:
    if not entity_types:
        return sorted(VALID_ENTITY_TYPES)
    # Validate entity types against the allowlist (case-insensitive)
    invalid = [
        t
        for t in entity_types
        if not isinstance(t, str) or t.lower() not in _ENTITY_TYPE_LOOKUP
    ]
    if invalid:
        raise ValueError(
            f"Invalid entity types: {invalid}. Allowed: {sorted(VALID_ENTITY_TYPES)}"
        )
    return sorted({_ENTITY_TYPE_LOOKUP[t.lower()] for t in entity_types})


def _validate_schemas(schemas: Sequence[str] | str) -> list[str]:
    if isinstance(schemas, str):
        schemas = [schemas]
    if not isinstance(schemas, list | tuple) or not schemas:
        raise ValueError("schemas must be a non-empty list of schema names")
    if len(schemas) > MAX_MAP_SCHEMAS:
        raise ValueError(
            f"A knowledge map may include at most {MAX_MAP_SCHEMAS} schemas"
        )
    result: list[str] = []
    for schema in schemas:
        if (
            not isinstance(schema, str)
            or not schema
            or "\x00" in schema
            or len(schema.encode()) > 63
        ):
            raise ValueError(f"Invalid schema name {schema!r}")
        if (
            schema.startswith("pg_")
            or schema == "information_schema"
            or schema == SEMANTIC_SCHEMA
        ):
            raise ValueError(
                f"System schema '{schema}' cannot be added to a knowledge map"
            )
        if schema not in result:
            result.append(schema)
    return result


def _check_entity_count(count: int) -> None:
    if count > MAX_MAP_ENTRIES:
        raise ValueError(
            f"The selected schemas contain {count} tables, views and columns; a "
            f"knowledge map supports at most {MAX_MAP_ENTRIES}. Use fewer schemas."
        )


async def _plan_sync(
    sql_driver: SqlDriver | SafeSqlDriver, knowledge_map: KnowledgeMap
) -> SyncPlan:
    entities = await read_catalog_entities(sql_driver, knowledge_map.schemas)
    _check_entity_count(len(entities))
    rows = await sql_driver.execute_query(_STORED_HASHES_SQL, [knowledge_map.name])
    stored: dict[EntityKey, str] = {
        (c["schema_name"], c["relation_name"], c["column_name"]): c["content_hash"]
        for c in (row.cells for row in (rows or []))
    }
    return plan_sync(entities, stored)


async def _embed_entities(
    embedder: EmbeddingProvider,
    entities: Sequence[CatalogEntity],
    knowledge_map: KnowledgeMap,
) -> list[list[float]]:
    if not entities:
        return []
    vectors = await embedder.embed([e.definition for e in entities])
    check_dimensions(
        f"Knowledge map '{knowledge_map.name}'", vectors[0], knowledge_map.dimensions
    )
    return vectors


async def _apply_plan(
    cur: Any, map_name: str, plan: SyncPlan, vectors: Sequence[Sequence[float]]
) -> None:
    if plan.to_delete:
        schemas, relations, columns = (
            list(x) for x in zip(*plan.to_delete, strict=True)
        )
        await cur.execute(_DELETE_ENTITIES_SQL, [schemas, relations, columns, map_name])
    for start in range(0, len(plan.to_upsert), _WRITE_CHUNK_SIZE):
        chunk = plan.to_upsert[start : start + _WRITE_CHUNK_SIZE]
        chunk_vectors = vectors[start : start + _WRITE_CHUNK_SIZE]
        await cur.execute(
            _UPSERT_ENTITIES_SQL,
            [
                map_name,
                [e.schema_name for e in chunk],
                [e.relation_name for e in chunk],
                [e.column_name or "" for e in chunk],
                [e.entity_type for e in chunk],
                [e.definition for e in chunk],
                [e.comment for e in chunk],
                [e.content_hash for e in chunk],
                [to_vector_literal(v) for v in chunk_vectors],
            ],
        )
