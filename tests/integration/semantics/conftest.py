"""Fixtures for semantic tool integration tests against a real pgvector database.

By default a ``pgvector/pgvector`` container is started (requires Docker).
Set ``PGVECTOR_TEST_DSN`` to use an existing database instead; it must allow
``CREATE EXTENSION vector`` and creating roles. Override the image with
``PGVECTOR_TEST_IMAGE``.
"""

import logging
import os
import time
from collections.abc import Generator

import psycopg
import pytest
import pytest_asyncio

from pgpacer.semantics.embeddings import HashEmbeddingProvider
from pgpacer.sql import SafeSqlDriver, SqlDriver

logger = logging.getLogger(__name__)

PGVECTOR_IMAGE = os.environ.get(
    "PGVECTOR_TEST_IMAGE", "pgvector/pgvector:0.8.2-pg17-trixie"
)


def _wait_for_postgres(dsn: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with psycopg.connect(dsn, connect_timeout=2) as conn:
                conn.execute("SELECT 1")
                return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


@pytest.fixture(scope="session")
def pgvector_dsn() -> Generator[str, None, None]:
    dsn = os.environ.get("PGVECTOR_TEST_DSN")
    if dsn:
        container = None
    else:
        try:
            import docker
            from docker import errors as docker_errors

            client = docker.from_env()
            client.ping()
        except Exception:
            pytest.skip("Docker is not available")

        try:
            container = client.containers.run(
                PGVECTOR_IMAGE,
                name=f"pgpacer-pgvector-test-{os.urandom(4).hex()}",
                environment={
                    "POSTGRES_PASSWORD": "test_password",
                    "POSTGRES_HOST_AUTH_METHOD": "trust",
                },
                ports={"5432/tcp": ("127.0.0.1", 0)},
                detach=True,
                remove=True,
            )
        except docker_errors.DockerException as e:
            pytest.skip(f"Could not start {PGVECTOR_IMAGE}: {e}")
        container.reload()
        port = container.attrs["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"]
        dsn = f"postgresql://postgres@127.0.0.1:{port}/postgres"
        logger.info(f"Started {PGVECTOR_IMAGE} on port {port}")

    try:
        _wait_for_postgres(dsn)
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        yield dsn
    finally:
        if container is not None:
            container.stop(timeout=5)


@pytest_asyncio.fixture
async def sql_driver(pgvector_dsn):
    """An unrestricted SqlDriver connected to the pgvector database."""
    driver = SqlDriver(engine_url=pgvector_dsn)
    driver.connect()
    try:
        yield driver
    finally:
        if driver.conn is not None:
            await driver.conn.close()


@pytest_asyncio.fixture
async def safe_driver(pgvector_dsn):
    """A restricted-mode SafeSqlDriver connected to the pgvector database."""
    base = SqlDriver(engine_url=pgvector_dsn)
    base.connect()
    try:
        yield SafeSqlDriver(sql_driver=base, timeout=30)
    finally:
        if base.conn is not None:
            await base.conn.close()


@pytest.fixture
def embedder():
    return HashEmbeddingProvider(dimensions=256)
