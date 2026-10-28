# pyright: reportPrivateUsage=false
"""Tests for embedding provider configuration of the semantic tools."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import mcp.types as types
import pytest
from pydantic import ValidationError

import pg_airman_mcp.server as server
from pg_airman_mcp.semantics.embeddings import (
    HashEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
)
from pg_airman_mcp.server import (
    ServerSettings,
    get_embedding_provider,
    search_knowledge_map,
)


class TestEmbeddingSettings:
    def test_defaults(self):
        settings = ServerSettings()

        assert settings.embedding_provider is None
        assert settings.embedding_model is None
        assert settings.embedding_base_url == "https://api.openai.com/v1"
        assert settings.embedding_api_key is None
        assert settings.embedding_dimensions is None
        assert settings.embedding_timeout == 30.0
        assert settings.embedding_batch_size == 32

    def test_env_vars(self):
        env = {
            "AIRMAN_MCP_EMBEDDING_PROVIDER": "OpenAI",
            "AIRMAN_MCP_EMBEDDING_MODEL": "nomic-embed-text",
            "AIRMAN_MCP_EMBEDDING_BASE_URL": "http://localhost:11434/v1",
            "AIRMAN_MCP_EMBEDDING_API_KEY": "sk-secret",
            "AIRMAN_MCP_EMBEDDING_DIMENSIONS": "768",
        }
        with patch.dict(os.environ, env):
            settings = ServerSettings()

        assert settings.embedding_provider == "openai"
        assert settings.embedding_model == "nomic-embed-text"
        assert settings.embedding_dimensions == 768
        assert settings.embedding_api_key is not None
        assert settings.embedding_api_key.get_secret_value() == "sk-secret"

    def test_api_key_is_not_exposed_in_repr(self):
        settings = ServerSettings(embedding_api_key="sk-secret")  # type: ignore[arg-type]

        assert "sk-secret" not in repr(settings)
        assert "sk-secret" not in str(settings.model_dump())

    def test_invalid_provider_rejected(self):
        with pytest.raises(ValidationError, match="embedding_provider must be one of"):
            ServerSettings(embedding_provider="magic")

    def test_empty_provider_means_unconfigured(self):
        assert ServerSettings(embedding_provider="").embedding_provider is None


class TestGetEmbeddingProvider:
    def test_unconfigured(self):
        with (
            patch.object(server, "embedding_provider", None),
            patch.object(server, "_embedding_config_error", None),
        ):
            with pytest.raises(ValueError, match="AIRMAN_MCP_EMBEDDING_PROVIDER"):
                get_embedding_provider()

    def test_misconfigured(self):
        with (
            patch.object(server, "embedding_provider", None),
            patch.object(server, "_embedding_config_error", "model is required"),
        ):
            with pytest.raises(ValueError, match="misconfigured: model is required"):
                get_embedding_provider()

    def test_configured(self):
        provider = HashEmbeddingProvider()
        with (
            patch.object(server, "embedding_provider", provider),
            patch.object(server, "_embedding_config_error", None),
        ):
            assert get_embedding_provider() is provider

    @pytest.mark.asyncio
    async def test_semantic_tool_reports_missing_provider(self):
        with (
            patch.object(server, "embedding_provider", None),
            patch.object(server, "_embedding_config_error", None),
            patch.object(server, "get_sql_driver", AsyncMock()),
        ):
            response = await search_knowledge_map(
                map_name="map",
                query="customers",
                min_similarity=0.7,
                entity_types=None,
                limit=20,
            )

        assert isinstance(response[0], types.TextContent)
        assert response[0].text.startswith("Error: No embedding provider")


async def _run_main(**embedding_settings):
    """Run main() with mocked transport/DB, returning the configured provider."""
    with (
        patch("pg_airman_mcp.server.ServerSettings") as mock_settings,
        patch("pg_airman_mcp.server.create_mcp_server") as mock_create,
        patch("pg_airman_mcp.server.db_connection") as mock_db,
        patch("pg_airman_mcp.server.shutdown_event") as mock_event,
        patch("sys.argv", ["server.py", "postgresql://test"]),
        patch("signal.signal"),
    ):
        settings = MagicMock()
        settings.database_url = "postgresql://test"
        settings.access_mode = "unrestricted"
        settings.transport = "stdio"
        settings.auth_enabled = False
        settings.purpose = None
        settings.determine_server_url = MagicMock(return_value=None)
        settings.get_required_scopes = MagicMock(return_value=[])
        settings.embedding_base_url = "https://api.openai.com/v1"
        settings.embedding_api_key = None
        settings.embedding_dimensions = None
        settings.embedding_timeout = 30.0
        settings.embedding_batch_size = 32
        for key, value in embedding_settings.items():
            setattr(settings, key, value)
        mock_settings.return_value = settings

        mcp = MagicMock()
        mcp.list_tools = AsyncMock(return_value=[])
        mcp.run_stdio_async = AsyncMock()
        mock_create.return_value = mcp
        mock_db.pool_connect = AsyncMock()
        mock_db.close = AsyncMock()
        mock_event.is_set = MagicMock(side_effect=[False, True])

        with pytest.raises(SystemExit):
            await server.main()
        return server.embedding_provider, server._embedding_config_error


class TestMainEmbeddingWiring:
    @pytest.fixture(autouse=True)
    def restore_globals(self):
        with (
            patch.object(server, "embedding_provider", None),
            patch.object(server, "_embedding_config_error", None),
        ):
            yield

    @pytest.mark.asyncio
    async def test_configures_hash_provider(self):
        provider, error = await _run_main(
            embedding_provider="hash", embedding_model=None
        )

        assert isinstance(provider, HashEmbeddingProvider)
        assert error is None

    @pytest.mark.asyncio
    async def test_configures_openai_provider(self):
        provider, error = await _run_main(
            embedding_provider="openai", embedding_model="text-embedding-3-small"
        )

        assert isinstance(provider, OpenAICompatibleEmbeddingProvider)
        assert provider.model_name == "text-embedding-3-small"
        assert error is None

    @pytest.mark.asyncio
    async def test_invalid_config_disables_semantic_tools_but_server_starts(self):
        provider, error = await _run_main(
            embedding_provider="openai", embedding_model=None
        )

        assert provider is None
        assert error is not None
        assert "EMBEDDING_MODEL is required" in error

    @pytest.mark.asyncio
    async def test_unconfigured(self):
        provider, error = await _run_main(embedding_provider=None, embedding_model=None)

        assert provider is None
        assert error is None
