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

    llm = ModelSettings(value("LLM_BASE_URL"), value("LLM_MODEL"), SecretStr(value("LLM_API_KEY")))
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
    return AISettings(llm=llm, embedding=embedding, reranker=reranker, limits=limits)
