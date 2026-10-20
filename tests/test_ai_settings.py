import asyncio
import os
import tempfile
import unittest
from threading import Event
from pathlib import Path
from unittest.mock import patch

from backend.ai_settings import load_ai_settings


class AISettingsTests(unittest.TestCase):
    def test_file_values_and_shared_embedding_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "LLM_BASE_URL=https://llm.example.test/v1\n"
                "LLM_BASE_MODEL=team-llm\nLLM_API_KEY=test-llm-secret\n"
                "EMBEDDING_BASE_URL=https://embedding.example.test/v1\n"
                "EMBEDDING_MODEL=team-embedding\nEMBEDDING_API_KEY=test-shared-secret\n"
                "LLM_LIGHT_MODEL=team-small\nRERANKER_MODEL=team-ranker\n",
                encoding="utf-8-sig",
            )
            settings = load_ai_settings(path, environ={})
        self.assertEqual(settings.llm.model, "team-llm")
        self.assertEqual(settings.llm.base_url, "https://llm.example.test/v1")
        self.assertEqual(settings.embedding.model, "team-embedding")
        self.assertEqual(settings.embedding.base_url, "https://embedding.example.test/v1")
        self.assertEqual(settings.reranker.model, "team-ranker")
        self.assertEqual(settings.reranker.base_url, settings.embedding.base_url)
        self.assertEqual(settings.light_llm.base_url, settings.llm.base_url)
        self.assertEqual(settings.light_llm.model, "team-small")
        self.assertIs(settings.light_llm.api_key, settings.llm.api_key)
        self.assertEqual(settings.llm.api_key.get_secret_value(), "test-llm-secret")
        self.assertEqual(settings.reranker.api_key.get_secret_value(), "test-shared-secret")
        self.assertIs(settings.reranker.api_key, settings.embedding.api_key)
        self.assertNotIn("test-llm-secret", repr(settings))
        self.assertNotIn("test-shared-secret", repr(settings))
        self.assertNotIn("test-shared-secret", str(settings.embedding.api_key))

    def test_process_environment_overrides_file_without_independent_reranker_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("LLM_BASE_MODEL=file-model\nEMBEDDING_API_KEY=file-secret\n", encoding="utf-8")
            settings = load_ai_settings(path, environ={
                "LLM_BASE_MODEL": "process-model", "EMBEDDING_API_KEY": "process-secret",
                "RERANKER_API_KEY": "ignored-secret",
            })
            empty = load_ai_settings(path, environ={"LLM_BASE_MODEL": "", "EMBEDDING_API_KEY": ""})
        self.assertEqual(settings.llm.model, "process-model")
        self.assertEqual(settings.reranker.api_key.get_secret_value(), "process-secret")
        self.assertEqual(empty.llm.model, "")
        self.assertEqual(empty.reranker.api_key.get_secret_value(), "")

    def test_blank_or_missing_file_keeps_models_unconfigured(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            missing = load_ai_settings(path, environ={})
            path.write_text("LLM_BASE_MODEL=\nEMBEDDING_API_KEY=\nRERANKER_MODEL=\n", encoding="utf-8")
            blank = load_ai_settings(path, environ={})
        self.assertEqual(missing, blank)
        self.assertEqual(blank.llm.api_key.get_secret_value(), "")
        self.assertEqual(blank.reranker.model, "")

    def test_model_alias_preserves_source_precedence_and_explicit_empty(self):
        cases = [
            ({'LLM_MODEL':'old-file'}, {}, 'old-file'),
            ({'LLM_MODEL':'old-file','LLM_BASE_MODEL':'new-file'}, {}, 'new-file'),
            ({'LLM_BASE_MODEL':'new-file'}, {'LLM_MODEL':'old-process'}, 'old-process'),
            ({'LLM_MODEL':'old-file'}, {'LLM_BASE_MODEL':'new-process'}, 'new-process'),
            ({'LLM_BASE_MODEL':'file'}, {'LLM_MODEL':'old','LLM_BASE_MODEL':'new'}, 'new'),
            ({'LLM_MODEL':'file'}, {'LLM_BASE_MODEL':''}, ''),
            ({'LLM_BASE_MODEL':'file'}, {'LLM_MODEL':''}, ''),
            ({'LLM_BASE_MODEL':'','LLM_MODEL':'old'}, {}, ''),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'.env'
            for file_values, process_values, expected in cases:
                with self.subTest(file=file_values, process=process_values):
                    path.write_text(''.join(f'{k}={v}\n' for k,v in file_values.items()),encoding='utf-8')
                    self.assertEqual(load_ai_settings(path,environ=process_values).llm.model,expected)

    def test_matching_legacy_connections_are_redundant_and_conflicts_fail_safely(self):
        values = dict(LLM_BASE_URL='https://shared.test/chat/completions',LLM_API_KEY='shared-key',
                      LLM_BASE_MODEL='main',LLM_LIGHT_MODEL='small',EMBEDDING_BASE_URL='https://vector.test/embedding',
                      EMBEDDING_API_KEY='vector-key',RERANKER_MODEL='rank')
        legacy = dict(LLM_LIGHT_BASE_URL=values['LLM_BASE_URL']+'/',LLM_LIGHT_API_KEY='shared-key',
                      RERANKER_BASE_URL=values['EMBEDDING_BASE_URL'])
        self.assertEqual(load_ai_settings(Path('absent-env'),environ=values),
                         load_ai_settings(Path('absent-env'),environ=values|legacy))
        for name in legacy:
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,name) as caught:
                load_ai_settings(Path('absent-env'),environ=values|{name:'private-conflicting-value'})
            self.assertNotIn('private-conflicting-value',str(caught.exception))
            self.assertNotIn('shared-key',str(caught.exception))

    def test_light_model_requires_shared_connection(self):
        for values in ({'LLM_LIGHT_MODEL':'small'},
                       {'LLM_LIGHT_MODEL':'small','LLM_BASE_URL':'https://shared.test'}):
            with self.subTest(values=values),self.assertRaisesRegex(ValueError,'LLM_BASE_URL and LLM_API_KEY'):
                load_ai_settings(Path('absent-env'),environ=values)

    def test_reranker_can_use_shared_connection_without_enabling_embedding(self):
        settings=load_ai_settings(Path('absent-env'),environ={
            'EMBEDDING_BASE_URL':'https://vector.test/embedding','EMBEDDING_API_KEY':'vector-key',
            'RERANKER_MODEL':'rank'})
        self.assertEqual(settings.embedding.model,'')
        self.assertEqual(settings.reranker.base_url,'https://vector.test/embedding')
        self.assertEqual(settings.reranker.model,'rank')

    def test_trailing_slash_uses_same_effective_embedding_and_reranker_endpoint(self):
        from backend.embedding_client import EmbeddingConfig
        settings=load_ai_settings(Path('absent-env'),environ={
            'EMBEDDING_BASE_URL':'https://vector.test/embedding/','EMBEDDING_MODEL':'vector',
            'EMBEDDING_API_KEY':'vector-key','RERANKER_MODEL':'rank'})
        self.assertEqual(EmbeddingConfig(settings.embedding).endpoint,settings.reranker.base_url)

    def test_literal_secret_and_loading_does_not_modify_process_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("LLM_API_KEY='test-${UNSET}-literal#value'\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                settings = load_ai_settings(path)
                self.assertEqual(dict(os.environ), {})
        self.assertEqual(settings.llm.api_key.get_secret_value(), "test-${UNSET}-literal#value")

    def test_fastapi_startup_loads_settings_without_model_calls(self):
        from backend.main import app, lifespan

        settings = load_ai_settings(Path("this-settings-test-file-does-not-exist"), environ={})

        async def startup():
            async with lifespan(app):
                self.assertIs(app.state.ai_settings, settings)

        # Keep this integration check independent of the user's actual corpus DB.
        with patch("backend.main.load_ai_settings", return_value=settings) as loader, \
             patch("backend.main.initialize"), patch("backend.main.connect"), \
             patch("backend.main.refresh_expert_policies"), \
             patch("backend.main.start_worker", return_value=(Event(),())):
            asyncio.run(startup())
            loader.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
