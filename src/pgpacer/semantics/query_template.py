"""Compile and validate golden query query templates.

A golden query is a reusable, read-only query containing ``${name}`` placeholders.
Placeholders compile to PostgreSQL bind parameters with an explicit cast to
the declared type, e.g. ``${email}`` becomes ``($1::text)``. Values supplied
at execution time are therefore always sent separately from the SQL text and
parsed by PostgreSQL's type input functions; they are never interpolated.

The compiled statement is parsed with pglast and must be a single SELECT
without data-modifying CTEs, ``SELECT INTO`` or row-locking clauses. This
check runs both when a golden query is created and every time it is executed,
because stored golden query rows are treated as untrusted input.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pglast
from pglast import ast

_PLACEHOLDER = re.compile(r"\$\{([^{}]*)\}")
_PARAM_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

MAX_QUERY_TEXT_CHARS = 20_000
MAX_DESCRIPTION_CHARS = 2_000
MAX_PARAMETERS = 32
MAX_VALUE_CHARS = 10_000
MAX_ARRAY_ITEMS = 1_000

# Accepted spellings -> canonical cast target. Only these types may be used,
# which keeps the generated cast text fixed and free of user input.
_SCALAR_TYPES = {
    "text": "text",
    "varchar": "varchar",
    "character varying": "varchar",
    "integer": "integer",
    "int": "integer",
    "int4": "integer",
    "bigint": "bigint",
    "int8": "bigint",
    "smallint": "smallint",
    "int2": "smallint",
    "numeric": "numeric",
    "decimal": "numeric",
    "real": "real",
    "float4": "real",
    "double precision": "double precision",
    "float": "double precision",
    "float8": "double precision",
    "boolean": "boolean",
    "bool": "boolean",
    "date": "date",
    "timestamp": "timestamp",
    "timestamp without time zone": "timestamp",
    "timestamptz": "timestamptz",
    "timestamp with time zone": "timestamptz",
    "time": "time",
    "interval": "interval",
    "uuid": "uuid",
}
_JSON_TYPES = {"json": "json", "jsonb": "jsonb"}
_PARAMETER_KEYS = frozenset({"name", "param_type", "description"})
_WRITE_STATEMENTS = (ast.InsertStmt, ast.UpdateStmt, ast.DeleteStmt, ast.MergeStmt)


@dataclass(frozen=True)
class QueryParameter:
    """A declared query parameter."""

    name: str
    param_type: str  # canonical type name
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "param_type": self.param_type,
            "description": self.description,
        }


@dataclass(frozen=True)
class CompiledQuery:
    """A golden query compiled to ``$n`` bind parameters."""

    sql: str
    parameters: tuple[QueryParameter, ...]


def allowed_param_types() -> list[str]:
    scalars = sorted(set(_SCALAR_TYPES.values()))
    return [*scalars, *sorted(_JSON_TYPES.values()), "<scalar type>[]"]


def parse_parameters(raw: Any) -> tuple[QueryParameter, ...]:
    """Validate parameter declarations (a list of ``{name, param_type,
    description}`` objects) and normalise their types."""
    if raw is None:
        return ()
    if not isinstance(raw, list | tuple):
        raise ValueError(
            "parameters must be a list of {name, param_type, description} objects"
        )
    if len(raw) > MAX_PARAMETERS:
        raise ValueError(
            f"A golden query may declare at most {MAX_PARAMETERS} parameters"
        )

    parsed: list[QueryParameter] = []
    seen: set[str] = set()
    for position, item in enumerate(raw, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"Parameter #{position} must be an object")
        unknown = set(item) - _PARAMETER_KEYS
        if unknown:
            raise ValueError(
                f"Parameter #{position} has unsupported keys: "
                f"{', '.join(sorted(map(str, unknown)))}"
            )
        name = item.get("name")
        if not isinstance(name, str) or not _PARAM_NAME.fullmatch(name):
            raise ValueError(
                f"Parameter #{position} has an invalid name {name!r}: use letters, "
                "digits and '_', starting with a letter or '_'"
            )
        if name in seen:
            raise ValueError(f"Duplicate parameter '{name}'")
        seen.add(name)
        if "param_type" not in item:
            raise ValueError(f"param_type is required for parameter '{name}'")
        param_type = _canonical_type(name, item["param_type"])
        description = item.get("description")
        if description is not None and (
            not isinstance(description, str) or len(description) > 1000
        ):
            raise ValueError(
                f"description of parameter '{name}' must be a string of at most "
                "1000 characters"
            )
        parsed.append(QueryParameter(name, param_type, description))
    return tuple(parsed)


def compile_query(
    query_text: str, parameters: Sequence[QueryParameter]
) -> CompiledQuery:
    """Compile ``${name}`` placeholders to typed bind parameters and validate
    that the result is a single read-only SELECT."""
    if not isinstance(query_text, str) or not query_text.strip():
        raise ValueError("query_text must not be empty")
    if len(query_text) > MAX_QUERY_TEXT_CHARS:
        raise ValueError(
            f"query_text must not exceed {MAX_QUERY_TEXT_CHARS} characters"
        )

    numbers = {p.name: i for i, p in enumerate(parameters, start=1)}
    used: set[str] = set()
    occurrences = 0

    def substitute(match: re.Match[str]) -> str:
        nonlocal occurrences
        name = match.group(1)
        number = numbers.get(name)
        if number is None:
            raise ValueError(f"Placeholder ${{{name}}} is not declared in parameters")
        used.add(name)
        occurrences += 1
        return f"(${number}::{parameters[number - 1].param_type})"

    sql = _PLACEHOLDER.sub(substitute, query_text.strip())
    unused = [p.name for p in parameters if p.name not in used]
    if unused:
        raise ValueError(
            f"Declared parameter(s) not used in query_text: {', '.join(unused)}"
        )
    _validate_read_only_select(sql, occurrences, len(parameters))
    return CompiledQuery(sql=sql, parameters=tuple(parameters))


def bind_values(compiled: CompiledQuery, values: Mapping[str, Any] | None) -> list[Any]:
    """Map caller-supplied values to bind parameters in ``$n`` order.

    Every declared parameter must be supplied and unknown names are rejected.
    Scalars are sent as text and converted by the ``$n::type`` cast, so
    PostgreSQL's own input functions validate them.
    """
    if values is None:
        values = {}
    if not isinstance(values, Mapping):
        raise ValueError("parameters must be an object of {param_name: value}")
    declared = {p.name for p in compiled.parameters}
    unknown = sorted(str(k)[:64] for k in values if k not in declared)
    if unknown:
        raise ValueError(f"Unknown parameter(s): {', '.join(unknown)}")

    bound: list[Any] = []
    for param in compiled.parameters:
        if param.name not in values:
            raise ValueError(
                f"Missing value for parameter '{param.name}' ({param.param_type})"
            )
        bound.append(_coerce(param, values[param.name]))
    return bound


def _canonical_type(name: str, raw: Any) -> str:
    if isinstance(raw, str):
        normalized = " ".join(raw.lower().split())
        is_array = normalized.endswith("[]")
        base = normalized[:-2].rstrip() if is_array else normalized
        if not is_array and base in _JSON_TYPES:
            return _JSON_TYPES[base]
        if base in _SCALAR_TYPES:
            return _SCALAR_TYPES[base] + ("[]" if is_array else "")
    raise ValueError(
        f"Unsupported param_type {raw!r} for parameter '{name}'. "
        f"Allowed: {', '.join(allowed_param_types())}"
    )


def _validate_read_only_select(sql: str, expected_refs: int, max_param: int) -> None:
    try:
        statements = pglast.parse_sql(sql)
    except pglast.parser.ParseError as e:
        raise ValueError(f"query_text is not valid SQL: {e}") from e
    if len(statements) != 1:
        raise ValueError("query_text must contain exactly one SQL statement")
    root = statements[0].stmt
    if not isinstance(root, ast.SelectStmt):
        raise ValueError(
            "query_text must be a SELECT query (WITH ... SELECT and VALUES are allowed)"
        )

    param_refs = 0
    for node in _walk(root):
        if isinstance(node, _WRITE_STATEMENTS):
            raise ValueError(
                "query_text must be read-only: INSERT, UPDATE, DELETE and MERGE "
                "are not allowed"
            )
        if isinstance(node, ast.SelectStmt):
            if node.intoClause is not None:
                raise ValueError("SELECT INTO is not allowed in query_text")
            if node.lockingClause:
                raise ValueError(
                    "Row-locking clauses (FOR UPDATE/SHARE) are not allowed in "
                    "query_text"
                )
        if isinstance(node, ast.ParamRef):
            param_refs += 1
            number = node.number if isinstance(node.number, int) else 0
            if not 1 <= number <= max_param:
                raise ValueError(_POSITIONAL_ERROR)
    if param_refs != expected_refs:
        raise ValueError(_POSITIONAL_ERROR)


_POSITIONAL_ERROR = (
    "query_text must use ${name} placeholders only: positional parameters ($1) "
    "are not allowed, and placeholders must not appear inside string literals "
    "or comments"
)


def _walk(node: ast.Node) -> Iterator[ast.Node]:
    stack: list[Any] = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, list | tuple):
            stack.extend(item)
        elif isinstance(item, ast.Node):
            yield item
            for attr in item.__slots__:
                value = getattr(item, attr, None)
                if isinstance(value, ast.Node | list | tuple):
                    stack.append(value)


def _coerce(param: QueryParameter, value: Any) -> Any:
    if value is None:
        return None
    if param.param_type in _JSON_TYPES:
        try:
            text = json.dumps(value)
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"Value for parameter '{param.name}' is not JSON serializable"
            ) from e
        return _check_length(param, text)
    if param.param_type.endswith("[]"):
        if not isinstance(value, list | tuple):
            raise ValueError(f"Value for parameter '{param.name}' must be a list")
        if len(value) > MAX_ARRAY_ITEMS:
            raise ValueError(
                f"Value for parameter '{param.name}' has more than "
                f"{MAX_ARRAY_ITEMS} items"
            )
        return [None if v is None else _scalar_text(param, v) for v in value]
    return _scalar_text(param, value)


def _scalar_text(param: QueryParameter, value: Any) -> str:
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, int | float | str):
        text = str(value)
    else:
        raise ValueError(
            f"Value for parameter '{param.name}' must be a string, number or boolean"
        )
    return _check_length(param, text)


def _check_length(param: QueryParameter, text: str) -> str:
    if len(text) > MAX_VALUE_CHARS:
        raise ValueError(
            f"Value for parameter '{param.name}' exceeds {MAX_VALUE_CHARS} characters"
        )
    return text
