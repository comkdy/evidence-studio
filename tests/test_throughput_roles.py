import tempfile
from pathlib import Path
from unittest import TestCase
from backend.ai_settings import load_ai_settings
from backend.answer_validation import validate_source_statement
from backend.wiki_store import WikiError


class ThroughputRolesTests(TestCase):
    def test_roles_preserve_shared_connections_and_context_budgets(self):
        with tempfile.TemporaryDirectory() as directory:
            values=load_ai_settings(Path(directory)/'missing',environ={
                'LLM_BASE_URL':'https://company.invalid/v1','LLM_API_KEY':'fixture-key','LLM_BASE_MODEL':'base',
                'LLM_CONTEXT_TOKENS':'32768','LLM_LIGHT_MODEL':'small',
                'LLM_BACKGROUND_MODEL':'background','LLM_REVIEW_MODEL':'review',
                'LLM_REVIEW_DEPLOYMENT_REVISION':'r2','LLM_REVIEW_MAX_OUTPUT_TOKENS':'2048'})
        self.assertEqual(values.for_role('answer').model,'base')
        self.assertEqual(values.for_role('extract').model,'background')
        self.assertEqual(values.for_role('lint').model,'review')
        for role in ('answer','background','review'):
            model=values.for_role(role)
            self.assertEqual(model.base_url,values.llm.base_url)
            self.assertIs(model.api_key,values.llm.api_key)
            self.assertEqual(model.resource_group,'shared')
            self.assertEqual(model.context_tokens,32768)
        self.assertEqual(values.for_role('review').max_output_tokens,2048)
        self.assertEqual(values.for_role('review').deployment_revision,'r2')
        self.assertEqual(values.limits.llm_max_concurrent_requests,1)

    def test_rejects_unsupported_quantities_and_conditions(self):
        source=['The test measured 100 mA at 90 C.']
        validate_source_statement('Measured 100 mA at 90 C.',source)
        for text in ('Measured 100 MA at 90 C.','Measured 100 mA at room temperature.','Measured 999 mA.'):
            with self.subTest(text=text),self.assertRaises(WikiError):
                validate_source_statement(text,source)
