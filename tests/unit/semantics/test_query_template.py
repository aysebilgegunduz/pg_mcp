"""Tests for golden query template compilation, validation and value binding.

These checks are the security boundary between caller-supplied golden query SQL /
parameter values and the database, so attack shapes are covered explicitly.
"""

import pytest

from pgpacer.semantics.query_template import (
    MAX_ARRAY_ITEMS,
    MAX_PARAMETERS,
    MAX_QUERY_TEXT_CHARS,
    MAX_VALUE_CHARS,
    QueryParameter,
    bind_values,
    compile_query,
    parse_parameters,
)


def params(*specs):
    return parse_parameters(
        [{"name": name, "param_type": type_} for name, type_ in specs]
    )


# ======================
# parse_parameters
# ======================


def test_parse_parameters_normalizes_types():
    parsed = parse_parameters(
        [
            {"name": "email", "param_type": "TEXT", "description": "Email"},
            {"name": "n", "param_type": "int"},
            {"name": "ts", "param_type": "Timestamp  With Time Zone"},
            {"name": "ids", "param_type": "int8[]"},
            {"name": "doc", "param_type": "jsonb"},
        ]
    )

    assert [p.param_type for p in parsed] == [
        "text",
        "integer",
        "timestamptz",
        "bigint[]",
        "jsonb",
    ]
    assert parsed[0].description == "Email"


def test_parse_parameters_none_is_empty():
    assert parse_parameters(None) == ()


@pytest.mark.parametrize(
    "param_type",
    [
        "text); DROP TABLE users; --",
        "regclass",
        "numeric(10,2)",
        "jsonb[]",
        "text[][]",
        "",
        None,
        42,
    ],
)
def test_parse_parameters_rejects_unsupported_types(param_type):
    with pytest.raises(ValueError, match="Unsupported param_type"):
        parse_parameters([{"name": "p", "param_type": param_type}])


@pytest.mark.parametrize(
    "name", ["1abc", "a-b", "a b", "a;b", "", None, "x" * 64, 'a"b']
)
def test_parse_parameters_rejects_invalid_names(name):
    with pytest.raises(ValueError, match="invalid name"):
        parse_parameters([{"name": name, "param_type": "text"}])


def test_parse_parameters_rejects_duplicates():
    with pytest.raises(ValueError, match="Duplicate parameter 'a'"):
        params(("a", "text"), ("a", "integer"))


def test_parse_parameters_rejects_unknown_keys():
    with pytest.raises(ValueError, match="unsupported keys: default"):
        parse_parameters([{"name": "a", "param_type": "text", "default": "x"}])


def test_parse_parameters_requires_param_type():
    with pytest.raises(ValueError, match="param_type is required"):
        parse_parameters([{"name": "a"}])


@pytest.mark.parametrize("raw", ["text", {"name": "a"}, ["a"]])
def test_parse_parameters_rejects_wrong_shapes(raw):
    with pytest.raises(ValueError):
        parse_parameters(raw)


def test_parse_parameters_limits_count():
    raw = [{"name": f"p{i}", "param_type": "text"} for i in range(MAX_PARAMETERS + 1)]
    with pytest.raises(ValueError, match="at most"):
        parse_parameters(raw)


# ======================
# compile_query
# ======================


def test_compile_substitutes_typed_bind_parameters():
    declared = params(("email", "text"), ("ids", "integer[]"))

    compiled = compile_query(
        "SELECT * FROM t WHERE email = ${email} AND id = ANY(${ids}) "
        "OR backup_email = ${email}",
        declared,
    )

    assert compiled.sql == (
        "SELECT * FROM t WHERE email = ($1::text) AND id = ANY(($2::integer[])) "
        "OR backup_email = ($1::text)"
    )
    assert compiled.parameters == declared


def test_compile_leaves_percent_signs_alone():
    compiled = compile_query("SELECT * FROM t WHERE a LIKE '%s%'", ())

    assert compiled.sql == "SELECT * FROM t WHERE a LIKE '%s%'"


@pytest.mark.parametrize(
    "query",
    [
        "SELECT 1",
        "SELECT 1;",
        "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
        "VALUES (1), (2)",
        "SELECT 1 UNION ALL SELECT 2",
        "SELECT * FROM (VALUES (1)) v(x) WHERE x IN (SELECT 1)",
    ],
)
def test_compile_accepts_read_only_queries(query):
    compile_query(query, ())


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("UPDATE t SET a = 1", "must be a SELECT"),
        ("DELETE FROM t", "must be a SELECT"),
        ("DROP TABLE t", "must be a SELECT"),
        ("CREATE TABLE x AS SELECT 1", "must be a SELECT"),
        ("COPY t TO PROGRAM 'id'", "must be a SELECT"),
        ("SET ROLE postgres", "must be a SELECT"),
        ("SELECT 1; DROP TABLE t", "exactly one SQL statement"),
        ("SELECT 1; SELECT 2", "exactly one SQL statement"),
        ("WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d", "read-only"),
        ("WITH i AS (INSERT INTO t VALUES (1) RETURNING *) SELECT 1", "read-only"),
        (
            "SELECT * FROM "
            "(WITH u AS (UPDATE t SET a=1 RETURNING a) SELECT a FROM u) s",
            "read-only",
        ),
        ("SELECT * INTO new_table FROM t", "SELECT INTO"),
        ("SELECT * FROM t FOR UPDATE", "Row-locking"),
        ("SELECT * FROM (SELECT * FROM t FOR SHARE) s", "Row-locking"),
        ("SELECT * FROM", "not valid SQL"),
    ],
)
def test_compile_rejects_unsafe_or_invalid_sql(query, message):
    with pytest.raises(ValueError, match=message):
        compile_query(query, ())


def test_compile_rejects_undeclared_placeholder():
    with pytest.raises(ValueError, match=r"\$\{email\} is not declared"):
        compile_query("SELECT * FROM t WHERE email = ${email}", ())


def test_compile_rejects_placeholder_with_sql_inside():
    with pytest.raises(ValueError, match="is not declared"):
        compile_query("SELECT ${1); DROP TABLE t; --}", ())


def test_compile_rejects_unused_parameters():
    with pytest.raises(ValueError, match="not used in query_text: b"):
        compile_query("SELECT ${a}", params(("a", "text"), ("b", "text")))


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM t WHERE a = $1",
        "SELECT * FROM t WHERE a = ${a} AND b = $1",
        "SELECT * FROM t WHERE a = ${a} AND b = $2",
    ],
)
def test_compile_rejects_positional_parameters(query):
    declared = params(("a", "text")) if "${a}" in query else ()
    with pytest.raises(ValueError, match="positional parameters"):
        compile_query(query, declared)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT '${a}'",
        "SELECT 1 -- ${a}",
        "SELECT 1 /* ${a} */",
        "SELECT $$${a}$$",
    ],
)
def test_compile_rejects_placeholders_in_literals_or_comments(query):
    with pytest.raises(ValueError, match="string literals or comments"):
        compile_query(query, params(("a", "text")))


@pytest.mark.parametrize("query", ["", "   ", None])
def test_compile_rejects_empty_query(query):
    with pytest.raises(ValueError, match="must not be empty"):
        compile_query(query, ())  # type: ignore[arg-type]


def test_compile_rejects_oversized_query():
    with pytest.raises(ValueError, match="must not exceed"):
        compile_query("SELECT 1 " + " " * MAX_QUERY_TEXT_CHARS, ())


# ======================
# bind_values
# ======================


def test_bind_values_orders_and_stringifies():
    compiled = compile_query(
        "SELECT ${n}, ${flag}, ${ratio}, ${name}, ${tags}, ${doc}",
        params(
            ("n", "integer"),
            ("flag", "boolean"),
            ("ratio", "numeric"),
            ("name", "text"),
            ("tags", "text[]"),
            ("doc", "jsonb"),
        ),
    )

    values = bind_values(
        compiled,
        {
            "doc": {"a": [1, 2]},
            "tags": ["x", None, 3],
            "name": "Alice",
            "ratio": 0.5,
            "flag": True,
            "n": 7,
        },
    )

    assert values == ["7", "true", "0.5", "Alice", ["x", None, "3"], '{"a": [1, 2]}']


def test_bind_values_keeps_injection_payloads_as_data():
    compiled = compile_query(
        "SELECT * FROM t WHERE email = ${email}", params(("email", "text"))
    )
    payload = "x' OR '1'='1'; DROP TABLE t; --"

    assert bind_values(compiled, {"email": payload}) == [payload]
    # The SQL text is unchanged by the value.
    assert payload not in compiled.sql


def test_bind_values_allows_null():
    compiled = compile_query("SELECT ${a}", params(("a", "integer")))

    assert bind_values(compiled, {"a": None}) == [None]


def test_bind_values_requires_every_parameter():
    compiled = compile_query("SELECT ${a}, ${b}", params(("a", "text"), ("b", "text")))

    with pytest.raises(ValueError, match="Missing value for parameter 'b'"):
        bind_values(compiled, {"a": "x"})


def test_bind_values_rejects_unknown_parameters():
    compiled = compile_query("SELECT ${a}", params(("a", "text")))

    with pytest.raises(ValueError, match="Unknown parameter"):
        bind_values(compiled, {"a": "x", "extra": "y"})


def test_bind_values_without_parameters():
    compiled = compile_query("SELECT 1", ())

    assert bind_values(compiled, None) == []
    assert bind_values(compiled, {}) == []


@pytest.mark.parametrize(
    ("param_type", "value", "message"),
    [
        ("text", {"a": 1}, "string, number or boolean"),
        ("integer", [1], "string, number or boolean"),
        ("integer[]", "1,2", "must be a list"),
        ("text[]", [{"x": 1}], "string, number or boolean"),
        ("jsonb", {1, 2}, "not JSON serializable"),
        ("text", "x" * (MAX_VALUE_CHARS + 1), "exceeds"),
        ("text[]", ["x"] * (MAX_ARRAY_ITEMS + 1), "more than"),
    ],
)
def test_bind_values_rejects_bad_values(param_type, value, message):
    compiled = compile_query("SELECT ${p}", params(("p", param_type)))

    with pytest.raises(ValueError, match=message):
        bind_values(compiled, {"p": value})


def test_bind_values_rejects_non_mapping():
    compiled = compile_query("SELECT ${p}", params(("p", "text")))

    with pytest.raises(ValueError, match="must be an object"):
        bind_values(compiled, ["x"])  # type: ignore[arg-type]


def test_golden_query_parameter_round_trips_through_dict():
    param = QueryParameter("email", "text", "Customer email")

    assert parse_parameters([param.to_dict()]) == (param,)
