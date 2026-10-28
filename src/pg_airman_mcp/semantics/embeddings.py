"""Embedding providers for the pgvector-backed semantic layer.

pgvector stores and compares vectors but, unlike AIDB, it does not generate
them. The MCP server therefore computes embeddings itself before writing
knowledge map entries or running a similarity search.

Two providers are available:

- ``openai``: any service implementing the OpenAI ``POST /embeddings`` API
  (OpenAI, Azure via LiteLLM, Ollama, vLLM, Hugging Face TEI, LM Studio, ...).
- ``hash``: a built-in, dependency-free feature-hashing embedder. It is
  deterministic and works offline, but captures lexical overlap only, not
  meaning. Intended for tests and air-gapped deployments.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

EMBEDDING_PROVIDERS = ("openai", "hash")

HASH_MODEL_NAME = "airman-hash-v1"
DEFAULT_HASH_DIMENSIONS = 512
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"

# pgvector can store up to 16,000 dimensions.
MAX_DIMENSIONS = 16000
# Inputs are truncated to keep requests within typical model context limits.
MAX_INPUT_CHARS = 8000

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_MAX_RETRY_AFTER_SECONDS = 10.0
_MAX_CONCURRENT_BATCHES = 4


class EmbeddingError(RuntimeError):
    """Raised when embeddings cannot be produced or fail validation."""


class EmbeddingProvider(ABC):
    """Base class handling batching and output validation for providers."""

    def __init__(
        self, model_name: str, dimensions: int | None, batch_size: int
    ) -> None:
        if not model_name:
            raise ValueError("Embedding model name must not be empty")
        if dimensions is not None and not 1 <= dimensions <= MAX_DIMENSIONS:
            raise ValueError(
                f"Embedding dimensions must be between 1 and {MAX_DIMENSIONS}, "
                f"got {dimensions}"
            )
        if batch_size < 1:
            raise ValueError(
                f"Embedding batch size must be at least 1, got {batch_size}"
            )
        self._model_name = model_name
        self._dimensions = dimensions
        self._batch_size = batch_size

    @property
    def model_name(self) -> str:
        """Identifier stored with every vector so incompatible spaces never mix."""
        return self._model_name

    @property
    def dimensions(self) -> int | None:
        """Vector size, or None until the first response reveals it."""
        return self._dimensions

    async def resolve_dimensions(self) -> int:
        """Return the vector size, probing the model once if it is not configured."""
        if self._dimensions is None:
            await self.embed(["dimension probe"])
        assert self._dimensions is not None
        return self._dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts, preserving order. Raises EmbeddingError on bad output."""
        if not texts:
            return []
        prepared = [_prepare_input(t) for t in texts]
        batches = [
            prepared[i : i + self._batch_size]
            for i in range(0, len(prepared), self._batch_size)
        ]
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_BATCHES)

        async def run(batch: list[str]) -> list[list[float]]:
            async with semaphore:
                vectors = await self._embed_batch(batch)
            return self._validate(vectors, expected=len(batch))

        try:
            # TaskGroup cancels in-flight batches as soon as one fails.
            async with asyncio.TaskGroup() as tg:
                tasks = [tg.create_task(run(b)) for b in batches]
        except ExceptionGroup as eg:
            raise eg.exceptions[0]  # noqa: B904 - surface the original error
        return [vector for task in tasks for vector in task.result()]

    async def embed_one(self, text: str) -> list[float]:
        """Embed a single text."""
        return (await self.embed([text]))[0]

    async def close(self) -> None:  # noqa: B027 - optional hook for subclasses
        """Release resources held by the provider."""

    @abstractmethod
    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in input order."""

    def _validate(self, vectors: Any, expected: int) -> list[list[float]]:
        if not isinstance(vectors, list) or len(vectors) != expected:
            got = len(vectors) if isinstance(vectors, list) else type(vectors).__name__
            raise EmbeddingError(f"Expected {expected} embeddings, got {got}")
        validated: list[list[float]] = []
        for vector in vectors:
            if not isinstance(vector, list) or not vector:
                raise EmbeddingError("Embedding is not a non-empty list of numbers")
            try:
                floats = [float(x) for x in vector]
            except (TypeError, ValueError) as e:
                raise EmbeddingError("Embedding contains non-numeric values") from e
            if not all(math.isfinite(x) for x in floats):
                raise EmbeddingError("Embedding contains NaN or infinite values")
            if not any(floats):
                # Cosine similarity is undefined for the zero vector.
                raise EmbeddingError("Embedding is the zero vector")
            if self._dimensions is None:
                if len(floats) > MAX_DIMENSIONS:
                    raise EmbeddingError(
                        f"Embedding has {len(floats)} dimensions; pgvector supports "
                        f"at most {MAX_DIMENSIONS}"
                    )
                self._dimensions = len(floats)
            elif len(floats) != self._dimensions:
                raise EmbeddingError(
                    f"Embedding has {len(floats)} dimensions, expected "
                    f"{self._dimensions}. Check the configured model and dimensions."
                )
            validated.append(floats)
        return validated


def _prepare_input(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("Embedding input must be a string")
    stripped = text.strip()
    if not stripped:
        raise ValueError("Embedding input must not be empty")
    return stripped[:MAX_INPUT_CHARS]


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """Calls an OpenAI-compatible ``POST {base_url}/embeddings`` endpoint."""

    def __init__(
        self,
        model: str,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        api_key: str | None = None,
        dimensions: int | None = None,
        timeout: float = 30.0,
        batch_size: int = 32,
        max_retries: int = 3,
    ) -> None:
        super().__init__(model, dimensions, batch_size)
        self._endpoint = _build_endpoint(base_url, has_api_key=bool(api_key))
        self._api_key = api_key
        # Only send "dimensions" when explicitly configured: many compatible
        # servers reject or ignore the parameter.
        self._request_dimensions = dimensions
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    def __repr__(self) -> str:
        # Never include the API key.
        return (
            f"OpenAICompatibleEmbeddingProvider(model={self.model_name!r}, "
            f"endpoint={self._endpoint!r})"
        )

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"User-Agent": "pg-airman-mcp"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=min(self._timeout, 10.0)),
                headers=headers,
                follow_redirects=False,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload: dict[str, Any] = {"model": self.model_name, "input": texts}
        if self._request_dimensions is not None:
            payload["dimensions"] = self._request_dimensions

        client = self._get_client()
        attempt = 0
        while True:
            try:
                response = await client.post(self._endpoint, json=payload)
            except httpx.TransportError as e:
                if attempt >= self._max_retries:
                    raise EmbeddingError(
                        f"Embedding request failed: {type(e).__name__}"
                    ) from e
                delay = _backoff(attempt)
            else:
                if response.status_code == 200:
                    return _parse_embeddings_response(response)
                if (
                    response.status_code not in _RETRYABLE_STATUS
                    or attempt >= self._max_retries
                ):
                    raise EmbeddingError(
                        f"Embedding request failed with HTTP {response.status_code}: "
                        f"{_error_detail(response)}"
                    )
                delay = _retry_after(response) or _backoff(attempt)
            attempt += 1
            logger.warning(
                "Embedding request failed (attempt %d/%d), retrying in %.1fs",
                attempt,
                self._max_retries + 1,
                delay,
            )
            await asyncio.sleep(delay)


def _build_endpoint(base_url: str, has_api_key: bool) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(
            f"Embedding base URL must be an http(s) URL, got: {base_url!r}"
        )
    if parsed.username or parsed.password:
        raise ValueError(
            "Embedding base URL must not embed credentials; "
            "use AIRMAN_MCP_EMBEDDING_API_KEY instead"
        )
    if parsed.scheme == "http" and parsed.hostname not in _LOOPBACK_HOSTS:
        if has_api_key:
            raise ValueError(
                "Refusing to send the embedding API key over unencrypted HTTP to "
                f"{parsed.hostname}; use an https:// base URL"
            )
        logger.warning(
            "Embedding endpoint %s uses unencrypted HTTP; schema metadata will be "
            "sent in cleartext",
            parsed.hostname,
        )
    return base_url.rstrip("/") + "/embeddings"


def _parse_embeddings_response(response: httpx.Response) -> list[list[float]]:
    try:
        body = response.json()
        data = body["data"]
        if not isinstance(data, list):
            raise TypeError("'data' is not a list")
        if all(isinstance(item, dict) and "index" in item for item in data):
            data = sorted(data, key=lambda item: item["index"])
        return [item["embedding"] for item in data]
    except (ValueError, KeyError, TypeError) as e:
        raise EmbeddingError("Malformed embeddings response") from e


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        message = body.get("error", body)
        if isinstance(message, dict):
            message = message.get("message", message)
        text = str(message)
    except ValueError:
        text = response.text
    return text[:200]


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return min(max(float(value), 0.0), _MAX_RETRY_AFTER_SECONDS)
    except ValueError:
        return None


def _backoff(attempt: int) -> float:
    return min(0.5 * 2**attempt, _MAX_RETRY_AFTER_SECONDS)


# ---------------------------------------------------------------------------
# Hash provider
# ---------------------------------------------------------------------------

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be by for from has in is it of on or that the their "
    "this to was were will with".split()
)
_TRIGRAM_WEIGHT = 0.3


class HashEmbeddingProvider(EmbeddingProvider):
    """Deterministic feature-hashing embedder (lexical similarity only).

    Text is split into lowercase word tokens (``snake_case`` and ``camelCase``
    identifiers are split too), lightly stemmed, and hashed together with
    character trigrams into a signed, L2-normalised vector.
    """

    def __init__(self, dimensions: int = DEFAULT_HASH_DIMENSIONS) -> None:
        if dimensions < 8:
            raise ValueError(
                f"Hash embedding dimensions must be >= 8, got {dimensions}"
            )
        super().__init__(HASH_MODEL_NAME, dimensions, batch_size=256)

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        dimensions = self._dimensions
        assert dimensions is not None
        # CPU-bound: keep the event loop free for other MCP requests.
        return await asyncio.to_thread(
            lambda: [_hash_embed(text, dimensions) for text in texts]
        )


def _hash_embed(text: str, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    features = list(_features(text)) or [(text.lower(), 1.0)]
    for feature, weight in features:
        digest = int.from_bytes(
            hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big"
        )
        sign = 1.0 if digest >> 63 else -1.0
        vector[digest % dimensions] += sign * weight
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0.0:
        # Pathological collision cancellation; fall back to a fixed axis.
        vector[0] = 1.0
        return vector
    return [x / norm for x in vector]


def _features(text: str):
    for token in _WORD.findall(_CAMEL_BOUNDARY.sub(" ", text).lower()):
        if token in _STOPWORDS:
            continue
        stem = _stem(token)
        yield f"w:{stem}", 1.0
        if len(stem) > 2:
            padded = f"#{stem}#"
            for i in range(len(padded) - 2):
                yield f"t:{padded[i : i + 3]}", _TRIGRAM_WEIGHT


def _stem(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_embedding_provider(
    provider: str | None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    dimensions: int | None = None,
    timeout: float = 30.0,
    batch_size: int = 32,
) -> EmbeddingProvider | None:
    """Build the configured provider, or return None when none is configured.

    Raises:
        ValueError: If the configuration is invalid.
    """
    if provider is None or provider == "":
        return None
    name = provider.lower()
    if name == "hash":
        if model and model != HASH_MODEL_NAME:
            logger.warning(
                "Ignoring embedding model %r: the hash provider always uses %s",
                model,
                HASH_MODEL_NAME,
            )
        return HashEmbeddingProvider(dimensions or DEFAULT_HASH_DIMENSIONS)
    if name == "openai":
        if not model:
            raise ValueError(
                "AIRMAN_MCP_EMBEDDING_MODEL is required for the 'openai' embedding "
                "provider (e.g. text-embedding-3-small, nomic-embed-text)"
            )
        if timeout <= 0:
            raise ValueError(f"Embedding timeout must be positive, got {timeout}")
        return OpenAICompatibleEmbeddingProvider(
            model=model,
            base_url=base_url or DEFAULT_OPENAI_BASE_URL,
            api_key=api_key,
            dimensions=dimensions,
            timeout=timeout,
            batch_size=batch_size,
        )
    raise ValueError(
        f"Unknown embedding provider {provider!r}; "
        f"expected one of {list(EMBEDDING_PROVIDERS)}"
    )
