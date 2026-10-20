"""Public routes use the real database and never serve demonstration papers."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.main import app
from backend.pilot_db import initialize


class ApplicationRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            'EVIDENCE_DB_PATH': str(Path(self.tmp.name) / 'empty.sqlite3'),
            'AUTH_MODE': 'local_admin',
            'BOOTSTRAP_ADMIN_EMPLOYEE_ID': '9000001',
            'LLM_BASE_URL': '', 'LLM_BASE_MODEL': '', 'LLM_API_KEY': '',
        })
        self.env.start()
        initialize()
        self.client = TestClient(app)  # Workers do not run against this fixture.

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.tmp.cleanup()

    def test_empty_database_remains_empty_through_the_real_library_api(self):
        response = self.client.get('/api/pilot/papers')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['papers'], [])
        self.assertEqual(response.json()['total'], 0)
        session = self.client.get('/api/auth/session')
        self.assertEqual(session.status_code, 200)
        self.assertEqual(session.json()['user']['employee_id'], '9000001')

    def test_removed_example_endpoints_are_not_served_or_documented(self):
        paths = self.client.get('/openapi.json').json()['paths']
        for path in ('/api/papers', '/api/papers/p1', '/api/ingestion/policy'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
                self.assertNotIn(path, paths)
        self.assertNotIn('/api/papers/{paper_id}', paths)
        self.assertIn('/api/pilot/papers', paths)

    def test_health_describes_the_active_database_service(self):
        response = self.client.get('/api/health')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['database_connected'])
        self.assertEqual(response.json()['pilot_api'], '/api/pilot')
        self.assertNotIn('prototype_api', response.json())
