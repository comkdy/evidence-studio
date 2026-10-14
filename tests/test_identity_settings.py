import tempfile
import unittest
from pathlib import Path
from backend.identity_settings import bootstrap_admin_id


class BootstrapSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / '.env'

    def tearDown(self):
        self.tmp.cleanup()

    def settings(self, text='', environ=None):
        self.path.write_text(text, encoding='utf-8')
        return bootstrap_admin_id(self.path, {} if environ is None else environ)

    def test_empty_local_configuration_uses_demo_identity(self):
        self.assertEqual(self.settings(), '0000000')

    def test_env_file_preserves_leading_zeroes(self):
        self.assertEqual(self.settings('BOOTSTRAP_ADMIN_EMPLOYEE_ID=0012345\n'), '0012345')

    def test_process_setting_overrides_env_file(self):
        self.assertEqual(self.settings('BOOTSTRAP_ADMIN_EMPLOYEE_ID=0012345\n',
                         {'BOOTSTRAP_ADMIN_EMPLOYEE_ID': '9000001'}), '9000001')

    def test_sso_requires_configured_non_demo_identity(self):
        for text in ('AUTH_MODE=sso\n', 'AUTH_MODE=sso\nBOOTSTRAP_ADMIN_EMPLOYEE_ID=0000000\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.settings(text)

    def test_sso_accepts_configured_identity(self):
        self.assertEqual(self.settings('AUTH_MODE=sso\nBOOTSTRAP_ADMIN_EMPLOYEE_ID=9000001\n'), '9000001')

    def test_invalid_identifiers_fail_without_echoing_input(self):
        for value in ('not-an-id', '1' * 33, '１２３', "1' OR 1=1"):
            with self.subTest(value=value), self.assertRaises(ValueError) as error:
                self.settings(environ={'BOOTSTRAP_ADMIN_EMPLOYEE_ID': value})
            self.assertNotIn(value, str(error.exception))

    def test_invalid_auth_mode_does_not_fall_back_to_demo_admin(self):
        with self.assertRaises(ValueError):
            self.settings('AUTH_MODE=invalid\n')
