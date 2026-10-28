"""A typed in-memory stand-in for SqlDriver used by the semantics unit tests."""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from pg_airman_mcp.sql import SqlDriver

Route = tuple[str, list[SqlDriver.RowResult] | None]


def rows(*dicts: dict[str, Any]) -> list[SqlDriver.RowResult]:
    return [SqlDriver.RowResult(cells=d) for d in dicts]


class FakeCursor:
    def __init__(self, fetchone_results: Sequence[Any]):
        self.execute = AsyncMock()
        self.fetchone = AsyncMock(side_effect=list(fetchone_results))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeDriver(SqlDriver):
    """Answers execute_query by SQL substring and records every call.

    ``transaction()`` yields a mock connection whose ``cursor()`` returns
    ``self.cursor``; opened transactions are recorded in ``transactions``.
    """

    def __init__(
        self,
        routes: Sequence[Route] = (),
        fetchone_results: Sequence[Any] = (),
        error: Exception | None = None,
    ):
        # SqlDriver.__init__ is skipped on purpose: no real connection is used.
        self.routes = list(routes)
        self.error = error
        self.calls: list[tuple[str, list[Any] | None]] = []
        self.transactions: list[bool] = []
        self.cursor = FakeCursor(fetchone_results)
        self.connection = MagicMock()
        self.connection.execute = AsyncMock()
        self.connection.cursor = MagicMock(return_value=self.cursor)

    async def execute_query(self, query, params=None, force_readonly=False):
        self.calls.append((query, params))
        if self.error is not None:
            raise self.error
        for needle, result in self.routes:
            if needle in query:
                return result
        raise AssertionError(f"Unexpected query: {query}")

    @asynccontextmanager
    async def transaction(self, *, readonly: bool = False) -> AsyncIterator[Any]:
        self.transactions.append(readonly)
        yield self.connection

    def last_params(self) -> list[Any]:
        params = self.calls[-1][1]
        assert params is not None, "last query had no parameters"
        return params

    def params_matching(self, needle: str) -> list[list[Any]]:
        """Parameters of every recorded query whose SQL contains ``needle``."""
        return [params or [] for query, params in self.calls if needle in query]
