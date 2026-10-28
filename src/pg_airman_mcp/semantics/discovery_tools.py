"""Combined discovery tool for SQL generation support."""

import logging
from typing import Any

from pg_airman_mcp.sql import SafeSqlDriver, SqlDriver

from .embeddings import EmbeddingProvider
from .golden_query_tools import search_golden_queries_by_vector
from .knowledge_map_tools import VALID_ENTITY_TYPES, search_knowledge_map_entries
from .store import (
    check_model,
    get_knowledge_map,
    validate_limit,
    validate_query,
    validate_similarity,
)

logger = logging.getLogger(__name__)


async def discover_schema_for_question(
    sql_driver: SqlDriver | SafeSqlDriver,
    embedder: EmbeddingProvider,
    map_name: str,
    question: str,
    include_golden_queries: bool = True,
    min_similarity: float = 0.6,
    max_results: int = 15,
) -> dict[str, Any]:
    """Combine knowledge map search and golden query discovery for SQL generation.

    Implements the "Knowledge map First" pattern in a single invocation,
    returning all context needed for text-to-SQL generation. The question is
    embedded once and reused for both searches.

    Args:
        sql_driver: SqlDriver or SafeSqlDriver instance.
        embedder: Embedding provider matching the knowledge map's model.
        map_name: Knowledge map to search.
        question: The business question to find schema elements for.
        include_golden_queries: Whether to also search golden queries.
        min_similarity: Minimum similarity threshold (0.0-1.0).
        max_results: Maximum results per category.

    Returns:
        Dict with schema_metadata and golden_queries lists.
    """
    validate_similarity(min_similarity)
    validate_limit(max_results)
    validate_query(question)

    knowledge_map = await get_knowledge_map(sql_driver, map_name)
    check_model(
        f"Knowledge map '{knowledge_map.name}'",
        knowledge_map.model_name,
        knowledge_map.dimensions,
        embedder,
    )
    vector = await embedder.embed_one(question)

    results: dict[str, Any] = {
        "schema_metadata": await search_knowledge_map_entries(
            sql_driver,
            knowledge_map,
            vector,
            min_similarity,
            sorted(VALID_ENTITY_TYPES),
            max_results,
        ),
        "golden_queries": [],
    }
    if include_golden_queries:
        results["golden_queries"] = await search_golden_queries_by_vector(
            sql_driver, knowledge_map, vector, min_similarity, max_results
        )

    logger.debug(
        f"Discovery in knowledge map '{knowledge_map.name}': "
        f"{len(results['schema_metadata'])} schema matches, "
        f"{len(results['golden_queries'])} golden query matches"
    )
    return results
