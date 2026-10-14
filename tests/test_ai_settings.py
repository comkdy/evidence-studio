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
                "LLM_MODEL=team-llm\nLLM_API_KEY=test-llm-secret\n"
                "EMBEDDING_BASE_URL=https://embedding.example.test/v1\n"
                "EMBEDDING_MODEL=team-embedding\nEMBEDDING_API_KEY=test-shared-secret\n"
                "RERANKER_BASE_URL=https://rank.example.test\nRERANKER_MODEL=team-ranker\n",
                encoding="utf-8-sig",
            )
            settings = load_ai_settings(path, environ={})
        self.assertEqual(settings.llm.model, "team-llm")
        self.assertEqual(settings.llm.base_url, "https://llm.example.test/v1")
        self.assertEqual(settings.embedding.model, "team-embedding")
        self.assertEqual(settings.embedding.base_url, "https://embedding.example.test/v1")
        self.assertEqual(settings.reranker.model, "team-ranker")
        self.assertEqual(settings.reranker.base_url, "https://rank.example.test")
        self.assertEqual(settings.llm.api_key.get_secret_value(), "test-llm-secret")
        self.assertEqual(settings.reranker.api_key.get_secret_value(), "test-shared-secret")
        self.assertIs(settings.reranker.api_key, settings.embedding.api_key)
        self.assertNotIn("test-llm-secret", repr(settings))
        self.assertNotIn("test-shared-secret", repr(settings))
        self.assertNotIn("test-shared-secret", str(settings.embedding.api_key))

    def test_process_environment_overrides_file_without_independent_reranker_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("LLM_MODEL=file-model\nEMBEDDING_API_KEY=file-secret\n", encoding="utf-8")
            settings = load_ai_settings(path, environ={
                "LLM_MODEL": "process-model", "EMBEDDING_API_KEY": "process-secret",
                "RERANKER_API_KEY": "ignored-secret",
            })
            empty = load_ai_settings(path, environ={"LLM_MODEL": "", "EMBEDDING_API_KEY": ""})
        self.assertEqual(settings.llm.model, "process-model")
        self.assertEqual(settings.reranker.api_key.get_secret_value(), "process-secret")
        self.assertEqual(empty.llm.model, "")
        self.assertEqual(empty.reranker.api_key.get_secret_value(), "")

    def test_blank_or_missing_file_keeps_models_unconfigured(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            missing = load_ai_settings(path, environ={})
            path.write_text("LLM_MODEL=\nEMBEDDING_API_KEY=\nRERANKER_MODEL=\n", encoding="utf-8")
            blank = load_ai_settings(path, environ={})
        self.assertEqual(missing, blank)
        self.assertEqual(blank.llm.api_key.get_secret_value(), "")
        self.assertEqual(blank.reranker.model, "")

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
