"""Read schema metadata from pg_catalog and describe it for embedding.

Each table, view and column becomes a :class:`CatalogEntity` whose
``definition`` is the text that gets embedded. The definition's hash is
stored next to the vector, so a refresh re-embeds only entities whose
description changed.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pg_airman_mcp.sql import SafeSqlDriver, SqlDriver

# Relation kinds included in a knowledge map. Partitions are skipped: their
# parent partitioned table already describes them.
_RELKIND_LABELS = {
    "r": ("Table", "Table"),
    "p": ("Table", "Partitioned table"),
    "f": ("Table", "Foreign table"),
    "v": ("View", "View"),
    "m": ("View", "Materialized view"),
}
_MAX_LISTED_COLUMNS = 50
_MAX_LISTED_CONSTRAINTS = 20
_MAX_DEFINITION_CHARS = 4000

EntityKey = tuple[str, str, str]  # (schema_name, relation_name, column_name or "")

_RELATIONS_SQL = """
    SELECT n.nspname AS schema_name,
           c.relname AS relation_name,
           c.relkind::text AS relkind,
           d.description AS comment
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_catalog.pg_description d
           ON d.objoid = c.oid
          AND d.classoid = 'pg_catalog.pg_class'::regclass
          AND d.objsubid = 0
    WHERE n.nspname = ANY(%s::text[])
      AND c.relkind IN ('r', 'p', 'f', 'v', 'm')
      AND NOT c.relispartition
    ORDER BY n.nspname, c.relname
"""

_COLUMNS_SQL = """
    SELECT n.nspname AS schema_name,
           c.relname AS relation_name,
           a.attname AS column_name,
           pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
           a.attnotnull AS not_null,
           d.description AS comment
    FROM pg_catalog.pg_attribute a
    JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_catalog.pg_description d
           ON d.objoid = c.oid
          AND d.classoid = 'pg_catalog.pg_class'::regclass
          AND d.objsubid = a.attnum
    WHERE n.nspname = ANY(%s::text[])
      AND c.relkind IN ('r', 'p', 'f', 'v', 'm')
      AND NOT c.relispartition
      AND a.attnum > 0
      AND NOT a.attisdropped
    ORDER BY n.nspname, c.relname, a.attnum
"""

_CONSTRAINTS_SQL = """
    SELECT n.nspname AS schema_name,
           c.relname AS relation_name,
           pg_catalog.pg_get_constraintdef(con.oid, true) AS definition
    FROM pg_catalog.pg_constraint con
    JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = ANY(%s::text[])
      AND con.contype IN ('p', 'f')
      AND NOT c.relispartition
    ORDER BY n.nspname, c.relname, con.contype, con.conname
"""


@dataclass(frozen=True)
class CatalogEntity:
    """A table, view or column as described to the embedding model."""

    entity_type: str  # "Table", "View" or "Column"
    schema_name: str
    relation_name: str
    column_name: str | None
    definition: str
    comment: str | None

    @property
    def key(self) -> EntityKey:
        return (self.schema_name, self.relation_name, self.column_name or "")

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.definition.encode()).hexdigest()


@dataclass
class SyncPlan:
    """Changes needed to bring stored entities in line with the catalog."""

    to_upsert: list[CatalogEntity] = field(default_factory=list)
    to_delete: list[EntityKey] = field(default_factory=list)
    added: int = 0
    updated: int = 0

    @property
    def pending(self) -> int:
        return len(self.to_upsert) + len(self.to_delete)


async def read_catalog_entities(
    sql_driver: SqlDriver | SafeSqlDriver, schemas: Sequence[str]
) -> list[CatalogEntity]:
    """Describe every table, view and column in the given schemas."""
    params = [list(schemas)]
    relations = await sql_driver.execute_query(_RELATIONS_SQL, params) or []
    columns = await sql_driver.execute_query(_COLUMNS_SQL, params) or []
    constraints = await sql_driver.execute_query(_CONSTRAINTS_SQL, params) or []
    return build_entities(
        [r.cells for r in relations],
        [c.cells for c in columns],
        [c.cells for c in constraints],
    )


def build_entities(
    relations: Iterable[Mapping[str, Any]],
    columns: Iterable[Mapping[str, Any]],
    constraints: Iterable[Mapping[str, Any]],
) -> list[CatalogEntity]:
    """Build entity descriptions from raw catalog rows."""
    columns_by_rel: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for col in columns:
        columns_by_rel[(col["schema_name"], col["relation_name"])].append(col)
    constraints_by_rel: dict[tuple[str, str], list[str]] = defaultdict(list)
    for con in constraints:
        constraints_by_rel[(con["schema_name"], con["relation_name"])].append(
            con["definition"]
        )

    entities: list[CatalogEntity] = []
    for rel in relations:
        labels = _RELKIND_LABELS.get(rel["relkind"])
        if labels is None:
            continue
        entity_type, label = labels
        schema, name = rel["schema_name"], rel["relation_name"]
        qualified = f"{schema}.{name}"
        rel_columns = columns_by_rel.get((schema, name), [])
        rel_comment = rel["comment"]

        parts = [f"{label} {qualified}"]
        if rel_comment:
            parts.append(f"Description: {rel_comment}")
        if rel_columns:
            listed = ", ".join(
                f"{c['column_name']} ({c['data_type']})"
                for c in rel_columns[:_MAX_LISTED_COLUMNS]
            )
            extra = len(rel_columns) - _MAX_LISTED_COLUMNS
            if extra > 0:
                listed += f", ... and {extra} more"
            parts.append(f"Columns: {listed}")
        rel_constraints = constraints_by_rel.get((schema, name), [])
        if rel_constraints:
            parts.append(
                "Constraints: " + "; ".join(rel_constraints[:_MAX_LISTED_CONSTRAINTS])
            )
        entities.append(
            CatalogEntity(
                entity_type=entity_type,
                schema_name=schema,
                relation_name=name,
                column_name=None,
                definition=_join(parts),
                comment=rel_comment,
            )
        )

        for col in rel_columns:
            nullability = " NOT NULL" if col["not_null"] else ""
            col_parts = [
                f"Column {qualified}.{col['column_name']}",
                f"Type: {col['data_type']}{nullability}",
                f"In {label.lower()} {qualified}"
                + (f" ({rel_comment})" if rel_comment else ""),
            ]
            if col["comment"]:
                col_parts.append(f"Description: {col['comment']}")
            entities.append(
                CatalogEntity(
                    entity_type="Column",
                    schema_name=schema,
                    relation_name=name,
                    column_name=col["column_name"],
                    definition=_join(col_parts),
                    comment=col["comment"],
                )
            )
    return entities


def plan_sync(
    entities: Sequence[CatalogEntity], stored_hashes: Mapping[EntityKey, str]
) -> SyncPlan:
    """Diff catalog entities against stored ``{key: content_hash}``."""
    plan = SyncPlan()
    current_keys = set()
    for entity in entities:
        current_keys.add(entity.key)
        stored = stored_hashes.get(entity.key)
        if stored is None:
            plan.to_upsert.append(entity)
            plan.added += 1
        elif stored != entity.content_hash:
            plan.to_upsert.append(entity)
            plan.updated += 1
    plan.to_delete = sorted(set(stored_hashes) - current_keys)
    return plan


def _join(parts: list[str]) -> str:
    return ". ".join(parts)[:_MAX_DEFINITION_CHARS]
