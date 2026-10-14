"""Server-only bootstrap identity; real employee IDs belong in the local .env."""
import os
import re
from pathlib import Path
from dotenv import dotenv_values

ENV_FILE = Path(__file__).resolve().parents[1] / '.env'
DEMO_ADMIN_ID = '0000000'


def bootstrap_admin_id(env_file=None, environ=None):
    values = {
        **dotenv_values(env_file if env_file is not None else ENV_FILE,
                        encoding='utf-8-sig', interpolate=False),
        **(os.environ if environ is None else environ),
    }
    mode = (values.get('AUTH_MODE') or 'local_admin').strip()
    if mode not in ('local_admin', 'sso'):
        raise ValueError('AUTH_MODE must be local_admin or sso.')
    value = (values.get('BOOTSTRAP_ADMIN_EMPLOYEE_ID') or '').strip()
    if not value:
        if mode == 'sso':
            raise ValueError('Set BOOTSTRAP_ADMIN_EMPLOYEE_ID before enabling SSO.')
        return DEMO_ADMIN_ID
    if not re.fullmatch(r'[0-9]{1,32}', value):
        raise ValueError('BOOTSTRAP_ADMIN_EMPLOYEE_ID must contain 1–32 ASCII digits.')
    if mode == 'sso' and value == DEMO_ADMIN_ID:
        raise ValueError('Use a real BOOTSTRAP_ADMIN_EMPLOYEE_ID before enabling SSO.')
    return value
