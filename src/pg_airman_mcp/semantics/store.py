"""Storage helpers shared by the pgvector knowledge map and golden query tools."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from functools import cache
from importlib import resources
from typing import Any

from pg_airman_mcp.sql import SafeSqlDriver, SqlDriver

from .embeddings import EmbeddingProvider

logger = logging.getLogger(__name__)

SEMANTIC_SCHEMA = "airman_semantic"

# Arbitrary constant ("airman" in ASCII) serialising concurrent installs.
_SCHEMA_LOCK_KEY = 0x6169726D616E

_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$")
MAX_SEARCH_LIMIT = 1000
MAX_QUERY_CHARS = 2000


@dataclass(frozen=True)
class KnowledgeMap:
    """A knowledge map row."""

    name: str
    model_name: str
    dimensions: int
    schemas: tuple[str, ...]
    refreshed_at: datetime | None = None


def jsonable(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of a result row with dates as ISO-8601 strings."""
    return {
        key: value.isoformat() if isinstance(value, date) else value
        for key, value in row.items()
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_name(kind: str, value: str) -> str:
    """Validate a knowledge map or golden query name.

    Names use letters, digits, '_' and '-' (max 63), starting with a letter or '_'.
    """
    if not isinstance(value, str) or not _NAME_PATTERN.fullmatch(value):
        raise ValueError(
            f"Invalid {kind} name {value!r}: use 1-63 letters, digits, '_' or '-', "
            "starting with a letter or '_'"
        )
    return value


def validate_similarity(min_similarity: float) -> None:
    if not (
        isinstance(min_similarity, int | float)
        and math.isfinite(min_similarity)
        and 0.0 <= min_similarity <= 1.0
    ):
        raise ValueError(
            f"min_similarity must be between 0.0 and 1.0, got {min_similarity}"
        )


def validate_limit(limit: int) -> None:
    if limit < 1:
        raise ValueError(f"limit must be at least 1, got {limit}")
    if limit > MAX_SEARCH_LIMIT:
        raise ValueError(f"limit must not exceed {MAX_SEARCH_LIMIT}, got {limit}")


def validate_query(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must not be empty")
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(f"query must not exceed {MAX_QUERY_CHARS} characters")
    return query


# ---------------------------------------------------------------------------
# Drivers
# ---------------------------------------------------------------------------


def require_unrestricted(
    sql_driver: SqlDriver | SafeSqlDriver, action: str
) -> SqlDriver:
    """Return the driver, refusing restricted-mode (SafeSqlDriver) callers."""
    if isinstance(sql_driver, SafeSqlDriver):
        raise ValueError(f"{action} is not permitted in restricted mode")
    return sql_driver


def base_driver(sql_driver: SqlDriver | SafeSqlDriver) -> SqlDriver:
    """Return the unrestricted driver underneath a SafeSqlDriver.

    Callers must have validated anything they execute on it.
    """
    if isinstance(sql_driver, SafeSqlDriver):
        return sql_driver.sql_driver
    return sql_driver


# ---------------------------------------------------------------------------
# Vectors and models
# ---------------------------------------------------------------------------


def to_vector_literal(vector: Sequence[float]) -> str:
    """Format a vector as pgvector text input, e.g. ``[0.1,0.2]``."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def check_model(
    owner: str, model_name: str, dimensions: int, embedder: EmbeddingProvider
) -> None:
    """Ensure the embedder produces vectors comparable with stored ones."""
    if embedder.model_name != model_name:
        raise ValueError(
            f"{owner} was embedded with model '{model_name}' but the server is "
            f"configured with '{embedder.model_name}'. Vectors from different "
            "models are not comparable: configure the original model or recreate it."
        )
    if embedder.dimensions is not None and embedder.dimensions != dimensions:
        raise ValueError(
            f"{owner} uses {dimensions}-dimensional vectors but the configured "
            f"model produces {embedder.dimensions}"
        )


def check_dimensions(owner: str, vector: Sequence[float], dimensions: int) -> None:
    if len(vector) != dimensions:
        raise ValueError(
            f"{owner} uses {dimensions}-dimensional vectors but the configured "
            f"model produced {len(vector)}"
        )


# ---------------------------------------------------------------------------
# Schema installation and knowledge map lookup
# ---------------------------------------------------------------------------


@cache
def schema_sql() -> str:
    """Return the DDL script creating the semantic storage schema."""
    return resources.files(__package__).joinpath("schema.sql").read_text()


async def is_installed(sql_driver: SqlDriver | SafeSqlDriver) -> bool:
    rows = await sql_driver.execute_query(
        "SELECT to_regclass(%s) IS NOT NULL AS installed",
        [f"{SEMANTIC_SCHEMA}.knowledge_maps"],
    )
    return bool(rows and rows[0].cells["installed"])


async def ensure_installed(sql_driver: SqlDriver) -> None:
    """Create the semantic storage schema if it does not exist yet.

    Checking first lets least-privilege roles use a schema a DBA provisioned:
    ``CREATE SCHEMA IF NOT EXISTS`` still requires CREATE on the database.
    """
    if await is_installed(sql_driver):
        return
    async with sql_driver.transaction() as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(%s)", [_SCHEMA_LOCK_KEY])
        cur = await conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        if await cur.fetchone() is None:
            raise ValueError(
                "The pgvector extension is not installed in this database. "
                "Ask a DBA to run: CREATE EXTENSION vector;"
            )
        cur = await conn.execute(
            "SELECT to_regclass(%s) IS NULL", [f"{SEMANTIC_SCHEMA}.knowledge_maps"]
        )
        row = await cur.fetchone()
        if row and row[0]:
            await conn.execute(schema_sql())  # type: ignore[arg-type]
            logger.info(f"Installed semantic storage schema '{SEMANTIC_SCHEMA}'")


async def get_knowledge_map(
    sql_driver: SqlDriver | SafeSqlDriver, map_name: str
) -> KnowledgeMap:
    """Load a knowledge map, raising ValueError if it does not exist."""
    validate_name("knowledge map", map_name)
    if not await is_installed(sql_driver):
        raise ValueError(f"Knowledge map '{map_name}' not found")
    rows = await sql_driver.execute_query(
        """
        SELECT name, model_name, dimensions, schemas, refreshed_at
        FROM airman_semantic.knowledge_maps
        WHERE name = %s
        """,
        [map_name],
    )
    if not rows:
        raise ValueError(f"Knowledge map '{map_name}' not found")
    cells = rows[0].cells
    return KnowledgeMap(
        name=cells["name"],
        model_name=cells["model_name"],
        dimensions=cells["dimensions"],
        schemas=tuple(cells["schemas"]),
        refreshed_at=cells["refreshed_at"],
    )
