"""Server-only model configuration. Loading settings never calls a model."""
from __future__ import annotations

import os
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values
from pydantic import SecretStr

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


@dataclass(frozen=True)
class ModelSettings:
    base_url: str
    model: str
    api_key: SecretStr = field(repr=False)
    context_tokens: int = 16384
    tokenizer_path: str = ""


@dataclass(frozen=True)
class InferenceLimits:
    llm_timeout_seconds: float = 300
    llm_queue_timeout_seconds: float = 300
    llm_max_concurrent_requests: int = 2
    research_timeout_seconds: float = 1500
    reranker_timeout_seconds: float = 30


@dataclass(frozen=True)
class AISettings:
    llm: ModelSettings
    embedding: ModelSettings
    reranker: ModelSettings
    limits: InferenceLimits = field(default_factory=InferenceLimits)
    light_llm: ModelSettings | None = None
    wiki_output_tokens: int = 2048


def load_ai_settings(
    env_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> AISettings:
    """Read the project .env regardless of cwd; process values take precedence.

    Empty values allow the existing corpus app to run without model services.
    Interpolation is disabled so a key containing ${...} stays literal. This
    function does not modify os.environ or expose settings via an HTTP route.
    """
    values = {
        **dotenv_values(env_file if env_file is not None else ENV_FILE,
                        encoding="utf-8-sig", interpolate=False),
        **(os.environ if environ is None else environ),
    }

    def value(name: str) -> str:
        return (values.get(name) or "").strip()

    def integer(name, default, low, high):
        try:
            result = int(value(name) or default)
            if not low <= result <= high:
                raise ValueError()
            return result
        except ValueError:
            raise ValueError(f"{name} must be an integer between {low} and {high}") from None

    llm = ModelSettings(value("LLM_BASE_URL"), value("LLM_MODEL"), SecretStr(value("LLM_API_KEY")),
        integer("LLM_CONTEXT_TOKENS", 16384, 4096, 1048576), value("LLM_TOKENIZER_PATH"))
    light = None
    if value("LLM_LIGHT_MODEL"):
        light_url = value("LLM_LIGHT_BASE_URL") or llm.base_url
        # Never send the main key to a separately configured endpoint implicitly.
        light_key = value("LLM_LIGHT_API_KEY") or (llm.api_key.get_secret_value()
            if light_url.rstrip("/") == llm.base_url.rstrip("/") else "")
        if not light_url or not light_key:
            raise ValueError("LLM_LIGHT_MODEL requires an endpoint and API key; a different endpoint needs LLM_LIGHT_API_KEY")
        light = ModelSettings(light_url, value("LLM_LIGHT_MODEL"), SecretStr(light_key),
            integer("LLM_LIGHT_CONTEXT_TOKENS", 16384, 4096, 1048576), value("LLM_LIGHT_TOKENIZER_PATH"))
    output_tokens = integer("WIKI_OUTPUT_TOKENS", 2048, 256, 16384)
    if any(output_tokens + 1024 >= s.context_tokens for s in (llm, light) if s):
        raise ValueError("WIKI_OUTPUT_TOKENS must leave room for Wiki instructions and evidence in each model context")
    embedding = ModelSettings(value("EMBEDDING_BASE_URL"), value("EMBEDDING_MODEL"), SecretStr(value("EMBEDDING_API_KEY")))
    reranker = ModelSettings(value("RERANKER_BASE_URL"), value("RERANKER_MODEL"), embedding.api_key)
    def seconds(name, default, upper=1800):
        try:
            result = float(value(name) or default)
            if not math.isfinite(result) or not 1 <= result <= upper:
                raise ValueError()
            return result
        except ValueError:
            raise ValueError(f"{name} must be between 1 and {upper} seconds") from None

    try:
        concurrency = int(value("LLM_MAX_CONCURRENT_REQUESTS") or 2)
        if not 1 <= concurrency <= 8:
            raise ValueError()
    except ValueError:
        raise ValueError("LLM_MAX_CONCURRENT_REQUESTS must be an integer between 1 and 8") from None
    limits = InferenceLimits(
        llm_timeout_seconds=seconds("LLM_TIMEOUT_SECONDS", 300),
        llm_queue_timeout_seconds=seconds("LLM_QUEUE_TIMEOUT_SECONDS", 300),
        llm_max_concurrent_requests=concurrency,
        research_timeout_seconds=seconds("RESEARCH_TIMEOUT_SECONDS", 1500, 7200),
        reranker_timeout_seconds=seconds("RERANKER_TIMEOUT_SECONDS", 30))
    return AISettings(llm=llm, embedding=embedding, reranker=reranker, limits=limits,
                      light_llm=light, wiki_output_tokens=output_tokens)
