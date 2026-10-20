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


def read_ai_environment(env_file=None, environ=None):
    """Normalize aliases within each source before applying environment precedence."""
    def normalized(source):
        values = dict(source)
        if "LLM_BASE_MODEL" not in values and "LLM_MODEL" in values:
            values["LLM_BASE_MODEL"] = values["LLM_MODEL"]
        return values

    return {
        **normalized(dotenv_values(env_file if env_file is not None else ENV_FILE,
                                   encoding="utf-8-sig", interpolate=False)),
        **normalized(os.environ if environ is None else environ),
    }


@dataclass(frozen=True)
class ModelSettings:
    base_url: str
    model: str
    api_key: SecretStr = field(repr=False)
    context_tokens: int = 16384
    tokenizer_path: str = ""
    resource_group: str = "shared"
    deployment_revision: str = ""
    max_output_tokens: int = 0


@dataclass(frozen=True)
class InferenceLimits:
    llm_timeout_seconds: float = 300
    llm_queue_timeout_seconds: float = 300
    llm_max_concurrent_requests: int = 1
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
    answer: ModelSettings | None = None
    background: ModelSettings | None = None
    review: ModelSettings | None = None

    def for_role(self, role):
        role = {'extract':'background','synthesis':'background','lint':'review'}.get(role,role)
        if role not in ('answer','background','review'):
            raise ValueError('Unknown model role')
        return getattr(self,role) or self.llm


def load_ai_settings(
    env_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> AISettings:
    """Read the project .env regardless of cwd; process values take precedence.

    Empty values allow the existing corpus app to run without model services.
    Interpolation is disabled so a key containing ${...} stays literal. This
    function does not modify os.environ or expose settings via an HTTP route.
    """
    values = read_ai_environment(env_file, environ)

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

    def shared_only(legacy, shared, *, url=False):
        previous, current = value(legacy), value(shared)
        if url:
            previous, current = previous.rstrip('/'), current.rstrip('/')
        if previous and previous != current:
            raise ValueError(f"{legacy} is no longer independent; remove it and configure {shared}")

    llm = ModelSettings(value("LLM_BASE_URL"), value("LLM_BASE_MODEL"), SecretStr(value("LLM_API_KEY")),
        integer("LLM_CONTEXT_TOKENS", 16384, 4096, 1048576), value("LLM_TOKENIZER_PATH"))
    light = None
    if value("LLM_LIGHT_MODEL"):
        shared_only("LLM_LIGHT_BASE_URL", "LLM_BASE_URL", url=True)
        shared_only("LLM_LIGHT_API_KEY", "LLM_API_KEY")
        if not llm.base_url or not llm.api_key.get_secret_value():
            raise ValueError("LLM_LIGHT_MODEL requires LLM_BASE_URL and LLM_API_KEY")
        light = ModelSettings(llm.base_url, value("LLM_LIGHT_MODEL"), llm.api_key,
            integer("LLM_LIGHT_CONTEXT_TOKENS", 16384, 4096, 1048576), value("LLM_LIGHT_TOKENIZER_PATH"))
    output_tokens = integer("WIKI_OUTPUT_TOKENS", 2048, 256, 16384)
    if any(output_tokens + 1024 >= s.context_tokens for s in (llm, light) if s):
        raise ValueError("WIKI_OUTPUT_TOKENS must leave room for Wiki instructions and evidence in each model context")
    embedding = ModelSettings(value("EMBEDDING_BASE_URL").rstrip("/"), value("EMBEDDING_MODEL"), SecretStr(value("EMBEDDING_API_KEY")))
    if value("RERANKER_MODEL"):
        shared_only("RERANKER_BASE_URL", "EMBEDDING_BASE_URL", url=True)
    reranker = ModelSettings(embedding.base_url, value("RERANKER_MODEL"), embedding.api_key)
    def seconds(name, default, upper=1800):
        try:
            result = float(value(name) or default)
            if not math.isfinite(result) or not 1 <= result <= upper:
                raise ValueError()
            return result
        except ValueError:
            raise ValueError(f"{name} must be between 1 and {upper} seconds") from None

    try:
        concurrency = int(value("LLM_MAX_CONCURRENT_REQUESTS") or 1)
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
    from dataclasses import replace
    llm = replace(llm, resource_group=value('LLM_RESOURCE_GROUP') or 'shared',
                  deployment_revision=value('LLM_DEPLOYMENT_REVISION'))
    if light:
        light = replace(light,resource_group=llm.resource_group,deployment_revision=value('LLM_LIGHT_DEPLOYMENT_REVISION') or llm.deployment_revision)
    embedding = replace(embedding,resource_group=value('EMBEDDING_RESOURCE_GROUP') or 'shared',
                        deployment_revision=value('EMBEDDING_DEPLOYMENT_REVISION'))
    reranker = replace(reranker,resource_group=value('RERANKER_RESOURCE_GROUP') or embedding.resource_group,
                       deployment_revision=value('RERANKER_DEPLOYMENT_REVISION'))
    def role_model(role):
        prefix='LLM_'+role.upper()
        # Roles use the same authenticated company endpoint. Only model and
        # resource scheduling differ; existing shared URL/key setup is preserved.
        model=replace(llm,model=value(prefix+'_MODEL') or llm.model,
            context_tokens=integer(prefix+'_CONTEXT_TOKENS',llm.context_tokens,4096,1048576),
            tokenizer_path=value(prefix+'_TOKENIZER_PATH') or llm.tokenizer_path,
            resource_group=value(prefix+'_RESOURCE_GROUP') or llm.resource_group,
            deployment_revision=value(prefix+'_DEPLOYMENT_REVISION') or llm.deployment_revision,
            max_output_tokens=integer(prefix+'_MAX_OUTPUT_TOKENS',0,0,262144))
        if model.max_output_tokens and model.max_output_tokens+1024>=model.context_tokens:
            raise ValueError(prefix+'_MAX_OUTPUT_TOKENS must leave room for instructions and evidence')
        return model
    return AISettings(llm=llm, embedding=embedding, reranker=reranker, limits=limits,
                      light_llm=light, wiki_output_tokens=output_tokens,
                      answer=role_model('answer'),background=role_model('background'),review=role_model('review'))
