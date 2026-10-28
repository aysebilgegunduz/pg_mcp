"""Tests for the embedding providers used by the pgvector semantic layer."""

import json
import math
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from pgpacer.semantics.embeddings import (
    HASH_MODEL_NAME,
    MAX_INPUT_CHARS,
    EmbeddingError,
    HashEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    create_embedding_provider,
)

ENDPOINT = "https://api.example.com/v1/embeddings"


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def ok_response(vectors):
    return httpx.Response(
        200,
        json={
            "data": [
                {"object": "embedding", "index": i, "embedding": v}
                for i, v in enumerate(vectors)
            ]
        },
    )


@pytest.fixture(autouse=True)
def no_sleep():
    """Retries back off with asyncio.sleep; skip the wait in tests."""
    with patch("pgpacer.semantics.embeddings.asyncio.sleep", AsyncMock()):
        yield


# ======================
# HashEmbeddingProvider
# ======================


@pytest.mark.asyncio
async def test_hash_embeddings_are_deterministic_and_normalized():
    provider = HashEmbeddingProvider(dimensions=256)
    first = await provider.embed_one("Customer email address")
    second = await HashEmbeddingProvider(dimensions=256).embed_one(
        "Customer email address"
    )

    assert first == second
    assert len(first) == 256
    assert math.isclose(math.sqrt(sum(x * x for x in first)), 1.0)
    assert provider.model_name == HASH_MODEL_NAME
    assert provider.dimensions == 256


@pytest.mark.asyncio
async def test_hash_embeddings_reflect_lexical_overlap():
    provider = HashEmbeddingProvider()
    query, related, unrelated = await provider.embed(
        [
            "find customer by email",
            "Find a customer by their email address",
            "quarterly revenue of completed orders",
        ]
    )

    assert cosine(query, related) > cosine(query, unrelated)


@pytest.mark.asyncio
async def test_hash_embeddings_split_identifiers_and_plurals():
    provider = HashEmbeddingProvider()
    snake, words = await provider.embed(["customer_emails", "customer email"])

    assert cosine(snake, words) > 0.99


@pytest.mark.asyncio
async def test_hash_embedding_of_punctuation_is_not_zero():
    vector = await HashEmbeddingProvider().embed_one("???")

    assert any(vector)


@pytest.mark.asyncio
async def test_embed_rejects_empty_input():
    provider = HashEmbeddingProvider()

    with pytest.raises(ValueError, match="must not be empty"):
        await provider.embed(["   "])


@pytest.mark.asyncio
async def test_embed_empty_list_returns_empty():
    assert await HashEmbeddingProvider().embed([]) == []


def test_hash_provider_rejects_tiny_dimensions():
    with pytest.raises(ValueError, match=">= 8"):
        HashEmbeddingProvider(dimensions=4)


# ==================================
# OpenAICompatibleEmbeddingProvider
# ==================================


@pytest.mark.asyncio
@respx.mock
async def test_openai_request_shape_and_auth():
    route = respx.post(ENDPOINT).mock(return_value=ok_response([[0.1, 0.2, 0.3]]))
    provider = OpenAICompatibleEmbeddingProvider(
        model="text-embedding-3-small",
        base_url="https://api.example.com/v1/",
        api_key="sk-secret",
    )

    vector = await provider.embed_one("  hello  ")

    assert vector == [0.1, 0.2, 0.3]
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer sk-secret"
    body = json.loads(request.content)
    assert body == {"model": "text-embedding-3-small", "input": ["hello"]}
    assert provider.dimensions == 3
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_openai_sends_dimensions_only_when_configured():
    route = respx.post(ENDPOINT).mock(return_value=ok_response([[0.5, 0.5]]))
    provider = OpenAICompatibleEmbeddingProvider(
        model="m", base_url="https://api.example.com/v1", dimensions=2
    )

    await provider.embed_one("x")

    assert json.loads(route.calls.last.request.content)["dimensions"] == 2
    assert "Authorization" not in route.calls.last.request.headers


@pytest.mark.asyncio
@respx.mock
async def test_openai_orders_results_by_index():
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )
    )
    provider = OpenAICompatibleEmbeddingProvider(
        model="m", base_url="https://api.example.com/v1"
    )

    assert await provider.embed(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]


@pytest.mark.asyncio
@respx.mock
async def test_openai_batches_requests_preserving_order():
    def respond(request):
        texts = json.loads(request.content)["input"]
        return ok_response([[float(t), 1.0] for t in texts])

    route = respx.post(ENDPOINT).mock(side_effect=respond)
    provider = OpenAICompatibleEmbeddingProvider(
        model="m", base_url="https://api.example.com/v1", batch_size=2
    )

    vectors = await provider.embed(["1", "2", "3", "4", "5"])

    assert [v[0] for v in vectors] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_openai_retries_rate_limits_and_server_errors():
    route = respx.post(ENDPOINT).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(503),
            ok_response([[1.0, 2.0]]),
        ]
    )
    provider = OpenAICompatibleEmbeddingProvider(
        model="m", base_url="https://api.example.com/v1"
    )

    assert await provider.embed_one("x") == [1.0, 2.0]
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_openai_gives_up_after_max_retries():
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(503))
    provider = OpenAICompatibleEmbeddingProvider(
        model="m", base_url="https://api.example.com/v1", max_retries=2
    )

    with pytest.raises(EmbeddingError, match="HTTP 503"):
        await provider.embed_one("x")
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_openai_client_error_is_not_retried_and_hides_api_key():
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(400, json={"error": {"message": "model not found"}})
    )
    provider = OpenAICompatibleEmbeddingProvider(
        model="m", base_url="https://api.example.com/v1", api_key="sk-secret"
    )

    with pytest.raises(EmbeddingError) as exc:
        await provider.embed_one("x")

    assert "model not found" in str(exc.value)
    assert "sk-secret" not in str(exc.value)
    assert "sk-secret" not in repr(provider)
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_openai_retries_transport_errors():
    route = respx.post(ENDPOINT).mock(
        side_effect=[httpx.ConnectError("boom"), ok_response([[1.0]])]
    )
    provider = OpenAICompatibleEmbeddingProvider(
        model="m", base_url="https://api.example.com/v1"
    )

    assert await provider.embed_one("x") == [1.0]
    assert route.call_count == 2


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"data": [{"embedding": [1.0]}, {"embedding": [1.0]}]}, "Expected 1"),
        ({"data": [{"embedding": [1.0, float("nan")]}]}, "NaN"),
        ({"data": [{"embedding": [0.0, 0.0]}]}, "zero vector"),
        ({"data": [{"embedding": ["a", "b"]}]}, "non-numeric"),
        ({"data": "nope"}, "Malformed"),
        ({"unexpected": True}, "Malformed"),
    ],
)
@pytest.mark.asyncio
@respx.mock
async def test_openai_rejects_invalid_responses(body, message):
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200, content=json.dumps(body, allow_nan=True).encode()
        )
    )
    provider = OpenAICompatibleEmbeddingProvider(
        model="m", base_url="https://api.example.com/v1"
    )

    with pytest.raises(EmbeddingError, match=message):
        await provider.embed_one("x")


@pytest.mark.asyncio
@respx.mock
async def test_openai_rejects_dimension_drift():
    respx.post(ENDPOINT).mock(
        side_effect=[ok_response([[1.0, 0.0]]), ok_response([[1.0, 0.0, 0.0]])]
    )
    provider = OpenAICompatibleEmbeddingProvider(
        model="m", base_url="https://api.example.com/v1"
    )

    await provider.embed_one("a")
    with pytest.raises(EmbeddingError, match="expected 2"):
        await provider.embed_one("b")


@pytest.mark.asyncio
@respx.mock
async def test_openai_truncates_long_inputs():
    route = respx.post(ENDPOINT).mock(return_value=ok_response([[1.0]]))
    provider = OpenAICompatibleEmbeddingProvider(
        model="m", base_url="https://api.example.com/v1"
    )

    await provider.embed_one("x" * (MAX_INPUT_CHARS + 100))

    sent = json.loads(route.calls.last.request.content)["input"][0]
    assert len(sent) == MAX_INPUT_CHARS


@pytest.mark.asyncio
@respx.mock
async def test_resolve_dimensions_probes_once():
    route = respx.post(ENDPOINT).mock(return_value=ok_response([[1.0, 2.0, 3.0]]))
    provider = OpenAICompatibleEmbeddingProvider(
        model="m", base_url="https://api.example.com/v1"
    )

    assert await provider.resolve_dimensions() == 3
    assert await provider.resolve_dimensions() == 3
    assert route.call_count == 1


@pytest.mark.parametrize(
    "url",
    ["ftp://example.com", "file:///etc/passwd", "not a url", "https://"],
)
def test_openai_rejects_non_http_urls(url):
    with pytest.raises(ValueError, match="http"):
        OpenAICompatibleEmbeddingProvider(model="m", base_url=url)


def test_openai_rejects_credentials_in_url():
    with pytest.raises(ValueError, match="credentials"):
        OpenAICompatibleEmbeddingProvider(
            model="m", base_url="https://user:pass@api.example.com/v1"
        )


def test_openai_refuses_api_key_over_plain_http_to_remote_host():
    with pytest.raises(ValueError, match="unencrypted HTTP"):
        OpenAICompatibleEmbeddingProvider(
            model="m", base_url="http://embeddings.internal/v1", api_key="sk"
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/v1",
        "http://127.0.0.1:8080/v1",
        "http://[::1]:8080/v1",
    ],
)
def test_openai_allows_api_key_over_http_to_loopback(url):
    OpenAICompatibleEmbeddingProvider(model="m", base_url=url, api_key="sk")


def test_openai_allows_keyless_http_to_internal_host():
    # e.g. a self-hosted Ollama or TEI container without authentication
    OpenAICompatibleEmbeddingProvider(model="m", base_url="http://tei:80/v1")


# ======================
# create_embedding_provider
# ======================


def test_factory_returns_none_when_unconfigured():
    assert create_embedding_provider(None) is None
    assert create_embedding_provider("") is None


def test_factory_builds_hash_provider():
    provider = create_embedding_provider("HASH", dimensions=128)

    assert isinstance(provider, HashEmbeddingProvider)
    assert provider.dimensions == 128


def test_factory_builds_openai_provider():
    provider = create_embedding_provider(
        "openai", model="nomic-embed-text", base_url="http://localhost:11434/v1"
    )

    assert isinstance(provider, OpenAICompatibleEmbeddingProvider)
    assert provider.model_name == "nomic-embed-text"


def test_factory_requires_model_for_openai():
    with pytest.raises(ValueError, match="EMBEDDING_MODEL is required"):
        create_embedding_provider("openai")


def test_factory_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Unknown embedding provider"):
        create_embedding_provider("magic")


@pytest.mark.parametrize("kwargs", [{"dimensions": 0}, {"dimensions": 20000}])
def test_factory_rejects_invalid_dimensions(kwargs):
    with pytest.raises(ValueError, match="dimensions"):
        create_embedding_provider("openai", model="m", **kwargs)
