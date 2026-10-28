"""Tests for catalog entity descriptions and refresh planning."""

from pgpacer.semantics.catalog import CatalogEntity, build_entities, plan_sync

RELATIONS = [
    {
        "schema_name": "sales",
        "relation_name": "customers",
        "relkind": "r",
        "comment": "Customer master data",
    },
    {
        "schema_name": "sales",
        "relation_name": "order_summary",
        "relkind": "m",
        "comment": None,
    },
    {"schema_name": "sales", "relation_name": "seq", "relkind": "S", "comment": None},
]
COLUMNS = [
    {
        "schema_name": "sales",
        "relation_name": "customers",
        "column_name": "id",
        "data_type": "integer",
        "not_null": True,
        "comment": None,
    },
    {
        "schema_name": "sales",
        "relation_name": "customers",
        "column_name": "email",
        "data_type": "character varying(255)",
        "not_null": False,
        "comment": "Customer email address",
    },
    {
        "schema_name": "sales",
        "relation_name": "order_summary",
        "column_name": "total",
        "data_type": "numeric",
        "not_null": False,
        "comment": None,
    },
]
CONSTRAINTS = [
    {
        "schema_name": "sales",
        "relation_name": "customers",
        "definition": "PRIMARY KEY (id)",
    }
]


def entities():
    return build_entities(RELATIONS, COLUMNS, CONSTRAINTS)


def by_key(items):
    return {e.key: e for e in items}


def test_build_entities_describes_relations_and_columns():
    result = by_key(entities())

    table = result[("sales", "customers", "")]
    assert table.entity_type == "Table"
    assert table.column_name is None
    assert table.comment == "Customer master data"
    assert table.definition == (
        "Table sales.customers. Description: Customer master data. "
        "Columns: id (integer), email (character varying(255)). "
        "Constraints: PRIMARY KEY (id)"
    )

    column = result[("sales", "customers", "email")]
    assert column.entity_type == "Column"
    assert column.comment == "Customer email address"
    assert column.definition == (
        "Column sales.customers.email. Type: character varying(255). "
        "In table sales.customers (Customer master data). "
        "Description: Customer email address"
    )
    assert "NOT NULL" in result[("sales", "customers", "id")].definition


def test_build_entities_maps_materialized_views_and_skips_other_kinds():
    result = by_key(entities())

    view = result[("sales", "order_summary", "")]
    assert view.entity_type == "View"
    assert view.definition.startswith("Materialized view sales.order_summary")
    assert ("sales", "seq", "") not in result
    assert len(result) == 5


def test_build_entities_caps_long_column_lists():
    columns = [
        {
            "schema_name": "s",
            "relation_name": "wide",
            "column_name": f"c{i}",
            "data_type": "text",
            "not_null": False,
            "comment": None,
        }
        for i in range(60)
    ]
    relations = [
        {"schema_name": "s", "relation_name": "wide", "relkind": "r", "comment": None}
    ]

    table = build_entities(relations, columns, [])[0]

    assert "c49 (text)" in table.definition
    assert "c50 (text)" not in table.definition
    assert "... and 10 more" in table.definition


def test_content_hash_tracks_definition():
    a = CatalogEntity("Table", "s", "t", None, "Table s.t", None)
    b = CatalogEntity("Table", "s", "t", None, "Table s.t. Description: x", None)

    assert a.content_hash != b.content_hash
    assert (
        a.content_hash
        == CatalogEntity("Table", "s", "t", None, "Table s.t", "c").content_hash
    )


def test_plan_sync_new_map_upserts_everything():
    plan = plan_sync(entities(), {})

    assert plan.added == 5
    assert plan.updated == 0
    assert plan.to_delete == []
    assert plan.pending == 5


def test_plan_sync_detects_added_changed_and_removed():
    current = entities()
    stored = {e.key: e.content_hash for e in current}
    stored[("sales", "customers", "email")] = "stale-hash"
    stored[("sales", "dropped", "")] = "old"
    del stored[("sales", "order_summary", "total")]

    plan = plan_sync(current, stored)

    assert plan.added == 1
    assert plan.updated == 1
    assert {e.key for e in plan.to_upsert} == {
        ("sales", "order_summary", "total"),
        ("sales", "customers", "email"),
    }
    assert plan.to_delete == [("sales", "dropped", "")]
    assert plan.pending == 3


def test_plan_sync_in_sync_has_nothing_pending():
    current = entities()

    assert plan_sync(current, {e.key: e.content_hash for e in current}).pending == 0
