"""Golden query tools for business SQL management and discovery.

A golden query is vetted, reusable business SQL: a named, read-only query with
``${param}`` placeholders, stored in ``airman_semantic.golden_queries`` together
with an embedding of its description so agents can discover it by
natural-language search.

Execution is hardened regardless of access mode:

- the stored query is re-validated as a single read-only SELECT;
- parameter values are sent as typed bind parameters, never interpolated;
- the query runs in a READ ONLY transaction;
- ``execute_as_role`` uses ``SET LOCAL ROLE``, scoped to that transaction.

In restricted mode the query must additionally pass SafeSqlDriver validation
and runs under the restricted-mode statement timeout.
"""

import json
import logging
from typing import Any

from psycopg import AsyncRawCursor
from psycopg import sql as psycopg_sql
from psycopg.rows import dict_row

from pg_airman_mcp.sql import SafeSqlDriver, SqlDriver
from pg_airman_mcp.sql.safe_sql import ALLOW_GOLDEN_QUERY_WRITE_IN_RESTRICTED

from .embeddings import EmbeddingProvider
from .query_template import (
    MAX_DESCRIPTION_CHARS,
    bind_values,
    compile_query,
    parse_parameters,
)
from .store import (
    KnowledgeMap,
    base_driver,
    check_dimensions,
    check_model,
    get_knowledge_map,
    is_installed,
    jsonable,
    to_vector_literal,
    validate_limit,
    validate_name,
    validate_query,
    validate_similarity,
)

logger = logging.getLogger(__name__)

# Golden queries from other models (or other dimensions of the same model) live in
# the same table; the CASE guarantees <=> only sees matching dimensions.
_SEARCH_SQL = """
    WITH q AS (SELECT %s::vector AS embedding, %s::integer AS dims)
    SELECT s.name, s.description, s.query_text, s.parameters, s.similarity
    FROM (
        SELECT a.name, a.description, a.query_text, a.parameters,
               CASE WHEN vector_dims(a.embedding) = q.dims
                    THEN 1 - (a.embedding <=> q.embedding)
               END AS similarity
        FROM airman_semantic.golden_queries a, q
        WHERE a.model_name = %s
          AND a.dimensions = q.dims
    ) s
    WHERE s.similarity >= %s
    ORDER BY s.similarity DESC, s.name
    LIMIT %s
"""


def _get_write_driver(
    sql_driver: SqlDriver | SafeSqlDriver,
) -> SqlDriver:
    """Get the appropriate driver for golden query write operations.

    In restricted mode, if ALLOW_GOLDEN_QUERY_WRITE_IN_RESTRICTED is enabled,
    bypasses SafeSqlDriver and uses the underlying SqlDriver directly
    (same pattern as execute_comment_on).
    """
    if isinstance(sql_driver, SafeSqlDriver):
        if not ALLOW_GOLDEN_QUERY_WRITE_IN_RESTRICTED:
            raise ValueError("Golden query writes are not permitted in restricted mode")
        logger.info(
            "Golden query write in restricted mode "
            "(ALLOW_GOLDEN_QUERY_WRITE_IN_RESTRICTED=true)"
        )
        return sql_driver.sql_driver
    return sql_driver


async def create_golden_query(
    sql_driver: SqlDriver | SafeSqlDriver,
    embedder: EmbeddingProvider,
    map_name: str,
    name: str,
    description: str,
    query_text: str,
    parameters: list[dict[str, Any]] | None = None,
) -> str:
    """Create a golden query for a reusable parameterized SQL query.

    The description is embedded with the knowledge map's model so the query is
    discoverable via search_golden_queries.

    Args:
        sql_driver: SqlDriver or SafeSqlDriver instance.
        embedder: Embedding provider matching the knowledge map's model.
        map_name: Name of the knowledge map (selects the model).
        name: Unique name for the golden query.
        description: Business description (vectorized for search).
        query_text: A read-only SELECT with ${param_name} placeholders.
        parameters: Optional list of {name, param_type, description}.

    Returns:
        Success message.
    """
    driver = _get_write_driver(sql_driver)
    validate_name("golden query", name)
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description must not be empty")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise ValueError(
            f"description must not exceed {MAX_DESCRIPTION_CHARS} characters"
        )
    declared = parse_parameters(parameters)
    compiled = compile_query(query_text, declared)
    if isinstance(sql_driver, SafeSqlDriver):
        # Fail early: restricted-mode execution applies the same check.
        sql_driver.validate_sql(compiled.sql)

    knowledge_map = await get_knowledge_map(sql_driver, map_name)
    check_model(
        f"Knowledge map '{knowledge_map.name}'",
        knowledge_map.model_name,
        knowledge_map.dimensions,
        embedder,
    )
    vector = await embedder.embed_one(description)
    check_dimensions(
        f"Knowledge map '{knowledge_map.name}'", vector, knowledge_map.dimensions
    )

    rows = await driver.execute_query(
        """
        INSERT INTO airman_semantic.golden_queries
            (name, description, query_text, parameters, model_name, dimensions,
             embedding)
        VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s::vector)
        ON CONFLICT (name) DO NOTHING
        RETURNING name
        """,
        [
            name,
            description.strip(),
            query_text.strip(),
            json.dumps([p.to_dict() for p in declared]),
            knowledge_map.model_name,
            knowledge_map.dimensions,
            to_vector_literal(vector),
        ],
    )
    if not rows:
        raise ValueError(f"Golden query '{name}' already exists")

    logger.info(f"Created golden query '{name}'")
    return f"Created golden query '{name}'"


async def delete_golden_query(
    sql_driver: SqlDriver | SafeSqlDriver,
    name: str,
) -> str:
    """Delete a golden query.

    Args:
        sql_driver: SqlDriver or SafeSqlDriver instance.
        name: Name of the golden query to delete.

    Returns:
        Success message.
    """
    driver = _get_write_driver(sql_driver)
    validate_name("golden query", name)
    rows = None
    if await is_installed(driver):
        rows = await driver.execute_query(
            "DELETE FROM airman_semantic.golden_queries WHERE name = %s RETURNING name",
            [name],
        )
    if not rows:
        raise ValueError(f"Golden query '{name}' not found")

    logger.info(f"Deleted golden query '{name}'")
    return f"Deleted golden query '{name}'"


async def search_golden_queries(
    sql_driver: SqlDriver | SafeSqlDriver,
    embedder: EmbeddingProvider,
    map_name: str,
    query: str,
    min_similarity: float = 0.7,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search for golden queries by natural language query.

    Only golden queries embedded with the same model as the knowledge map are
    considered.

    Args:
        sql_driver: SqlDriver or SafeSqlDriver instance.
        embedder: Embedding provider matching the knowledge map's model.
        map_name: Name of the knowledge map.
        query: Natural language description of what you're looking for.
        min_similarity: Minimum cosine similarity threshold (0.0-1.0).
        limit: Maximum results to return (1-1000).

    Returns:
        List of matching golden queries with name, description, query_text,
        parameters and similarity.
    """
    validate_similarity(min_similarity)
    validate_limit(limit)
    validate_query(query)

    knowledge_map = await get_knowledge_map(sql_driver, map_name)
    check_model(
        f"Knowledge map '{knowledge_map.name}'",
        knowledge_map.model_name,
        knowledge_map.dimensions,
        embedder,
    )
    vector = await embedder.embed_one(query)
    return await search_golden_queries_by_vector(
        sql_driver, knowledge_map, vector, min_similarity, limit
    )


async def search_golden_queries_by_vector(
    sql_driver: SqlDriver | SafeSqlDriver,
    knowledge_map: KnowledgeMap,
    vector: list[float],
    min_similarity: float,
    limit: int,
) -> list[dict[str, Any]]:
    """Run a golden query similarity search with an already-computed embedding."""
    check_dimensions(
        f"Knowledge map '{knowledge_map.name}'", vector, knowledge_map.dimensions
    )
    rows = await sql_driver.execute_query(
        _SEARCH_SQL,
        [
            to_vector_literal(vector),
            knowledge_map.dimensions,
            knowledge_map.model_name,
            min_similarity,
            limit,
        ],
    )
    results = [row.cells for row in (rows or [])]
    logger.info(
        f"Golden query search in knowledge map '{knowledge_map.name}' "
        f"returned {len(results)} results"
    )
    return results


async def execute_golden_query(
    sql_driver: SqlDriver | SafeSqlDriver,
    name: str,
    parameters: dict[str, Any] | None = None,
    execute_as_role: str | None = None,
) -> list[dict[str, Any]]:
    """Execute a golden query with provided parameters.

    Args:
        sql_driver: SqlDriver or SafeSqlDriver instance.
        name: Name of the golden query to execute.
        parameters: Parameter values as {param_name: value}.
        execute_as_role: Optional role to execute as (via SET LOCAL ROLE; the
            connecting user must be a member of it).

    Returns:
        Query result rows.
    """
    validate_name("golden query", name)
    if execute_as_role is not None:
        _validate_role(execute_as_role)

    stored = await _get_golden_query(sql_driver, name)
    # Stored rows are untrusted: recompile and revalidate on every execution.
    compiled = compile_query(
        stored["query_text"], parse_parameters(stored["parameters"])
    )
    values = bind_values(compiled, parameters)

    timeout_ms: int | None = None
    if isinstance(sql_driver, SafeSqlDriver):
        sql_driver.validate_sql(compiled.sql)
        if sql_driver.timeout:
            timeout_ms = int(sql_driver.timeout * 1000)

    async with base_driver(sql_driver).transaction(readonly=True) as conn:
        if timeout_ms:
            await conn.execute(
                psycopg_sql.SQL("SET LOCAL statement_timeout = {}").format(
                    psycopg_sql.Literal(timeout_ms)
                )
            )
        if execute_as_role:
            await conn.execute(
                psycopg_sql.SQL("SET LOCAL ROLE {}").format(
                    psycopg_sql.Identifier(execute_as_role)
                )
            )
        # A raw cursor uses PostgreSQL-native $n placeholders, so '%' in the
        # query SQL needs no escaping.
        async with AsyncRawCursor(conn, row_factory=dict_row) as cur:
            # The SQL text is fixed by the validated query; values are bound.
            await cur.execute(compiled.sql, values)  # type: ignore[arg-type]
            rows = await cur.fetchall() if cur.description else []

    logger.info(f"Executed golden query '{name}', returned {len(rows)} rows")
    return [dict(row) for row in rows]


async def list_golden_queries(
    sql_driver: SqlDriver | SafeSqlDriver,
) -> list[dict[str, Any]]:
    """List all available golden queries.

    Args:
        sql_driver: SqlDriver or SafeSqlDriver instance.

    Returns:
        List of golden queries with name, description, query_text, parameters,
        param_count and model_name.
    """
    if not await is_installed(sql_driver):
        return []
    rows = await sql_driver.execute_query(
        """
        SELECT name, description, query_text, parameters,
               jsonb_array_length(parameters) AS param_count,
               model_name, created_by, created_at
        FROM airman_semantic.golden_queries
        ORDER BY name
        """
    )
    results = [jsonable(row.cells) for row in (rows or [])]
    logger.info(f"Found {len(results)} golden queries")
    return results


async def _get_golden_query(
    sql_driver: SqlDriver | SafeSqlDriver, name: str
) -> dict[str, Any]:
    rows = None
    if await is_installed(sql_driver):
        rows = await sql_driver.execute_query(
            "SELECT query_text, parameters FROM airman_semantic.golden_queries "
            "WHERE name = %s",
            [name],
        )
    if not rows:
        raise ValueError(f"Golden query '{name}' not found")
    return rows[0].cells


def _validate_role(role: str) -> None:
    if (
        not isinstance(role, str)
        or not role
        or "\x00" in role
        or len(role.encode()) > 63
    ):
        raise ValueError(f"Invalid role name {role!r}")
