"""Explicitly configured internal /embeddings client; never defaults to a public service."""
from __future__ import annotations

import hashlib
import json
import math
import ssl
import time
from dataclasses import dataclass, field
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .ai_settings import ModelSettings
from .embedding_adapter import build_request, parse_response
from .pilot_db import now


class EmbeddingError(ValueError):
    """Safe to show in a job result: no response body, input text, URL or credentials."""


@dataclass(frozen=True)
class EmbeddingConfig:
    service: ModelSettings = field(repr=False)
    revision: str = ""
    input_prefix: str = field(default="", repr=False)
    max_input_tokens: int = 512
    batch_size: int = 1
    timeout_seconds: float = 30
    ca_bundle: str = field(default="", repr=False)
    endpoint_url: str = field(default="", repr=False)
    api_format: str = "company"
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    contract_json: str = field(default="", repr=False)

    def __post_init__(self):
        if self.api_format not in ("company", "openai_compatible", "tei", "custom_json"):
            raise EmbeddingError("Confirm the internal API schema and set EMBEDDING_API_FORMAT before semantic indexing")
        if self.api_format == "company" and self.batch_size != 1:
            raise EmbeddingError("The confirmed company embedding API requires one input string per request")
        try:
            parsed = urlsplit(self.endpoint)
            parsed.port
        except ValueError:
            raise EmbeddingError("Invalid internal embedding endpoint") from None
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise EmbeddingError("Set EMBEDDING_BASE_URL to the internal service URL without credentials or query parameters")
        if not self.service.model.strip():
            raise EmbeddingError("EMBEDDING_MODEL is required for semantic chunking")
        if not 128 <= self.max_input_tokens <= 32768 or not 1 <= self.batch_size <= 128 or not 0 < self.timeout_seconds <= 1800:
            raise EmbeddingError("Invalid embedding input limit, batch size or timeout")
        if not self.auth_header or any(c in self.auth_header for c in "\r\n: ") or any(c in self.auth_scheme for c in "\r\n"):
            raise EmbeddingError("Invalid embedding authentication header configuration")
        if self.api_format == "custom_json":
            try:
                contract = json.loads(self.contract_json)
                assert isinstance(contract["request"], dict)
                if contract.get("response") is None:
                    raise EmbeddingError("Embedding request is configured, but response mapping is unconfirmed; fill the response in EMBEDDING_CONTRACT_PATH before connecting")
                for key in ("items_path", "vector_path"):
                    assert isinstance(contract["response"][key], list)
                    assert all(isinstance(x, str) for x in contract["response"][key])
                assert contract["response"].get("index_field") is None or isinstance(contract["response"]["index_field"], str)
                assert type(contract["response"].get("single_vector", False)) is bool
                if contract["response"].get("single_vector"):
                    assert self.batch_size == 1 and not contract["response"].get("index_field")
            except EmbeddingError:
                raise
            except (ValueError, KeyError, TypeError, AssertionError):
                raise EmbeddingError("Invalid EMBEDDING_CONTRACT_PATH JSON mapping") from None

    @property
    def endpoint(self):
        if self.endpoint_url:
            return self.endpoint_url
        base = self.service.base_url.rstrip("/")
        if self.api_format in ("company", "custom_json") or base.endswith(("/embedding", "/embeddings", "/embed")):
            return base
        return base + ("/embed" if self.api_format == "tei" else "/embeddings")

    def identity(self):
        # Keys and transport timing do not change vector meaning and never enter metadata.
        return {"adapter": "internal-json-v1", "api_format": self.api_format, "model": self.service.model,
                "revision": self.revision, "endpoint_sha256": hashlib.sha256(self.endpoint.encode()).hexdigest(),
                "prefix_sha256": hashlib.sha256(self.input_prefix.encode()).hexdigest(),
                "contract_sha256": hashlib.sha256(self.contract_json.encode()).hexdigest()}


def validate_vectors(vectors, count, dimensions=None):
    if not isinstance(vectors, list) or len(vectors) != count:
        raise EmbeddingError("Embedding response count does not match the input batch")
    normalized = []
    for vector in vectors:
        if not isinstance(vector, list) or not 1 <= len(vector) <= 65536:
            raise EmbeddingError("Embedding response contains an invalid vector")
        if dimensions is None:
            dimensions = len(vector)
        try:
            valid = all(type(x) in (int, float) and math.isfinite(x) for x in vector)
        except OverflowError:
            valid = False
        if len(vector) != dimensions or not valid:
            raise EmbeddingError("Embedding response has mixed dimensions or non-finite values")
        scale = math.hypot(*vector)
        if not math.isfinite(scale) or scale == 0:
            raise EmbeddingError("Embedding response contains a zero or invalid vector")
        normalized.append([x / scale for x in vector])
    return normalized


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Do not forward the key or document text to another destination.


class EmbeddingClient:
    def __init__(self, config):
        self.config = config
        try:
            context = ssl.create_default_context(cafile=config.ca_bundle or None)
        except (OSError, ssl.SSLError):
            raise EmbeddingError("Cannot load EMBEDDING_CA_BUNDLE") from None
        self.opener = build_opener(NoRedirect(), HTTPSHandler(context=context))
        self.dimensions = None
        self.requests = 0

    def embed(self, texts):
        if not texts:
            return []
        result = []
        for start in range(0, len(texts), self.config.batch_size):
            batch = texts[start:start + self.config.batch_size]
            try:
                body = json.dumps(build_request(self.config, batch), ensure_ascii=False).encode("utf-8")
            except (ValueError, TypeError, KeyError):
                raise EmbeddingError("Cannot build embedding request; check the API contract and batch size") from None
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            key = self.config.service.api_key.get_secret_value()
            if key:
                if any(ord(c) < 32 or ord(c) == 127 for c in key):
                    raise EmbeddingError("Embedding API key contains invalid control characters")
                headers[self.config.auth_header] = (self.config.auth_scheme + " " if self.config.auth_scheme else "") + key
            for attempt in range(3):
                try:
                    request = Request(self.config.endpoint, data=body, headers=headers, method="POST")
                    self.requests += 1
                    with self.opener.open(request, timeout=self.config.timeout_seconds) as response:
                        raw = response.read(16 * 1024 * 1024 + 1)
                    if len(raw) > 16 * 1024 * 1024:
                        raise EmbeddingError("Embedding response is too large")
                    break
                except HTTPError as exc:
                    code = exc.code
                    exc.close()
                    if code in (429, 502, 503) and attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                    raise EmbeddingError(f"Embedding API returned HTTP {code}; the previous index is preserved") from None
                except (URLError, TimeoutError, OSError, HTTPException) as error:
                    if isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError):
                        # The provider may still be computing; do not duplicate slow requests.
                        raise EmbeddingError("Embedding API timed out; no automatic resend; the previous index is preserved") from None
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                    raise EmbeddingError("Embedding API connection failed or timed out; the previous index is preserved") from None
                except EmbeddingError:
                    raise
                except ValueError:
                    raise EmbeddingError("Invalid embedding URL or HTTP header configuration") from None
            try:
                vectors = parse_response(self.config, json.loads(raw), len(batch))
            except (ValueError, KeyError, TypeError, AttributeError):
                raise EmbeddingError("Embedding API response does not match the configured JSON contract") from None
            vectors = validate_vectors(vectors, len(batch), self.dimensions)
            self.dimensions = len(vectors[0])
            result.extend(vectors)
        return result


class CachedEmbedder:
    """Buffered builds read cache only; publish new vectors with the finished index."""
    def __init__(self, db, config, client=None, *, buffered=False):
        self.db, self.config = db, config
        self.buffered, self.pending = buffered, {}
        self.client = client or EmbeddingClient(config)
        self.identity = hashlib.sha256(json.dumps(config.identity(), sort_keys=True).encode()).hexdigest()
        self.cache_hits, self.new_vectors = 0, 0
        row = db.execute("SELECT dimensions FROM chunk_embedding_cache WHERE model_fingerprint=? LIMIT 1", (self.identity,)).fetchone()
        self.dimensions = row[0] if row else None

    def embed(self, texts):
        hashes = [hashlib.sha256(text.encode()).hexdigest() for text in texts]
        keys = [hashlib.sha256((self.identity + digest).encode()).hexdigest() for digest in hashes]
        known, missing = {}, {}
        for key, digest, text in zip(keys, hashes, texts):
            if key in known or key in missing:
                continue
            if key in self.pending:
                known[key] = json.loads(self.pending[key][3])
                self.cache_hits += 1
                continue
            row = self.db.execute("SELECT vector_json FROM chunk_embedding_cache WHERE cache_key=?", (key,)).fetchone()
            if row:
                try:
                    vector = json.loads(row[0])
                except ValueError:
                    raise EmbeddingError("Invalid cached embedding; rebuild the affected model cache") from None
                vector = validate_vectors([vector], 1, self.dimensions)[0]
                self.dimensions = len(vector)
                known[key] = vector
                self.cache_hits += 1
            else:
                missing[key] = (digest, text)
        if missing:
            if self.db.in_transaction:
                raise EmbeddingError("Embedding preparation requires a connection without an open transaction")
            vectors = validate_vectors(self.client.embed([v[1] for v in missing.values()]), len(missing), self.dimensions)
            self.dimensions = len(vectors[0])
            for (key, (digest, _)), vector in zip(missing.items(), vectors):
                known[key] = vector
                row = (key, self.identity, digest, json.dumps(vector), self.dimensions, now())
                if self.buffered:
                    self.pending[key] = row
                else:
                    self.db.execute("INSERT OR IGNORE INTO chunk_embedding_cache VALUES(?,?,?,?,?,?)", row)
                self.new_vectors += 1
        return [known[key] for key in keys]


    def flush(self, db):
        """No network here. Caller includes cache and index in one short transaction."""
        dimensions = db.execute("SELECT DISTINCT dimensions FROM chunk_embedding_cache WHERE model_fingerprint=?", (self.identity,)).fetchall()
        if any(row[0] != self.dimensions for row in dimensions):
            raise EmbeddingError("Embedding dimensions changed during index preparation")
        db.executemany("INSERT OR IGNORE INTO chunk_embedding_cache VALUES(?,?,?,?,?,?)", self.pending.values())
