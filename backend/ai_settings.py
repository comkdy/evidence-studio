"""Server-only model configuration. Loading settings never calls a model."""
from __future__ import annotations

import os
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
class AISettings:
    llm: ModelSettings
    embedding: ModelSettings
    reranker: ModelSettings


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
    return AISettings(llm=llm, embedding=embedding, reranker=reranker)
