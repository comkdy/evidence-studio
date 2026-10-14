import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend.main import app
from backend.pilot_db import initialize,connect,now
from backend.sso_adapter import EmployeeProfile


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Path(self.tmp.name)/'users.sqlite3'
        self.env=patch.dict(os.environ,{'EVIDENCE_DB_PATH':str(self.db),'AUTH_MODE':'local_admin','SSO_COOKIE_NAME':'company_sso','BOOTSTRAP_ADMIN_EMPLOYEE_ID':'9000001'})
        self.env.start();initialize(self.db)
        # Lifespan workers are deliberately not run against the fixture database.
        self.client=TestClient(app)
    def tearDown(self):
        self.client.close();self.env.stop();self.tmp.cleanup()
    def create(self,id='3000000',role='user'):
        response=self.client.post('/api/users',json={'employee_id':id,'role':role})
        self.assertEqual(response.status_code,201,response.text)
        return response.json()
    def sso(self,id):
        self.client.cookies.set('company_sso','opaque-test-token')
        stack=__import__('contextlib').ExitStack()
        stack.enter_context(patch.dict(os.environ,{'AUTH_MODE':'sso'}))
        stack.enter_context(patch('backend.sso_adapter.verify_sso_cookie',return_value=id))
        stack.enter_context(patch('backend.sso_adapter.fetch_employee_profile',return_value=EmployeeProfile(id,'테스트 이름','테스트 팀')))
        return stack

    def test_temporary_admin_is_seeded_once_and_ignores_client_identity_headers(self):
        result=self.client.get('/api/auth/session',headers={'X-Employee-Id':'9999999','X-Role':'user'}).json()
        self.assertEqual(result['mode'],'local_admin');self.assertEqual(result['user']['employee_id'],'9000001')
        self.assertTrue(result['user']['permissions']['manage_users']);self.assertEqual(result['user']['name'],'')
        with connect(self.db) as db:
            db.execute("UPDATE users SET role='superuser' WHERE employee_id='9000001'");db.commit()
        initialize(self.db)
        with connect(self.db) as db:self.assertEqual(db.execute("SELECT role FROM users WHERE employee_id='9000001'").fetchone()[0],'superuser')

    def test_processing_status_can_be_read_by_authenticated_regular_user(self):
        with self.sso('3000001'):
            response=self.client.get('/api/pilot/processing')
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['watcher']['interval_seconds'],60)
        with patch.dict(os.environ,{'AUTH_MODE':'sso'}):
            self.client.cookies.clear()
            self.assertEqual(self.client.get('/api/pilot/processing').status_code,401)

    def test_admin_registration_update_conflict_and_audit(self):
        user=self.create()
        self.assertEqual(user['name'],'');self.assertEqual(user['profile_source'],'pending')
        change={'role':'superuser','active':False,'revision':user['revision']}
        changed=self.client.patch('/api/users/3000000',json=change)
        self.assertEqual(changed.status_code,200,changed.text);self.assertFalse(changed.json()['active'])
        self.assertEqual(self.client.patch('/api/users/3000000',json=change).status_code,409)
        self.assertEqual(self.client.post('/api/users',json={'employee_id':'3000000'}).status_code,409)
        self.assertEqual(self.client.post('/api/users',json={'employee_id':'not-a-number'}).status_code,422)
        self.assertEqual(self.client.post('/api/users',json={'employee_id':'1234567','name':'spoof'}).status_code,422)
        with connect(self.db) as db:self.assertEqual(db.execute('SELECT count(*) FROM user_audit').fetchone()[0],2)

    def test_self_demotion_and_deactivation_are_blocked(self):
        for role,active in [('user',True),('admin',False)]:
            self.assertEqual(self.client.patch('/api/users/9000001',json={'role':role,'active':active,'revision':1}).status_code,409)

    def test_sso_is_closed_when_missing_unimplemented_or_invalid(self):
        with patch.dict(os.environ,{'AUTH_MODE':'sso'}):
            self.assertEqual(self.client.get('/api/auth/session').status_code,401)
            self.client.cookies.set('company_sso','9000001')
            self.assertEqual(self.client.get('/api/auth/session').status_code,503)
            with patch('backend.sso_adapter.verify_sso_cookie',side_effect=HTTPException(401,'인증 실패')):
                self.assertEqual(self.client.get('/api/pilot/papers').status_code,401)
        with patch.dict(os.environ,{'AUTH_MODE':'invalid'}):
            self.assertEqual(self.client.get('/api/auth/session').status_code,503)

    def test_new_sso_user_gets_profile_and_least_privilege(self):
        with self.sso('4000000'):
            session=self.client.get('/api/auth/session').json()
            self.assertEqual(session['user']['role'],'user');self.assertEqual(session['user']['name'],'테스트 이름')
            self.assertEqual(session['user']['team'],'테스트 팀')
            self.assertEqual(self.client.get('/api/users').status_code,403)
            self.assertEqual(self.client.get('/api/pilot/experts/options').status_code,403)
            self.assertEqual(self.client.post('/api/pilot/wiki-maintenance/retry',headers={'Origin':'http://testserver'}).status_code,403)
            self.assertEqual(self.client.get('/api/pilot/papers').status_code,200)
            self.assertEqual(self.client.post('/api/users',json={'employee_id':'4444444','role':'admin'},headers={'Origin':'http://testserver'}).status_code,403)

    def test_superuser_can_manage_experts_and_knowledge_but_not_users(self):
        self.create(role='superuser')
        with self.sso('3000000'):
            self.assertEqual(self.client.get('/api/pilot/experts/options').status_code,200)
            self.assertTrue(self.client.get('/api/auth/session').json()['user']['permissions']['manage_knowledge'])
            self.assertFalse(self.client.get('/api/auth/session').json()['user']['permissions']['refresh_wiki'])
            self.assertEqual(self.client.post('/api/pilot/wiki-maintenance/retry',headers={'Origin':'http://testserver'}).status_code,403)
            self.assertEqual(self.client.get('/api/users').status_code,403)
            self.assertFalse(self.client.get('/api/auth/session').json()['user']['permissions']['manage_users'])
            # Cached directory data does not cache application permissions.
            with connect(self.db) as db:
                db.execute("UPDATE users SET role='user' WHERE employee_id='3000000'");db.commit()
            self.assertEqual(self.client.get('/api/pilot/experts/options').status_code,403)

    def test_only_admin_can_queue_or_inspect_scoped_wiki_refresh(self):
        self.assertEqual(self.client.post('/api/pilot/wiki-maintenance/retry').status_code,410)
        common=self.client.post('/api/pilot/wiki-maintenance/common')
        self.assertEqual(common.status_code,202)
        repeated=self.client.post('/api/pilot/wiki-maintenance/common')
        self.assertEqual(common.json()['job_id'],repeated.json()['job_id'])
        expert=self.client.post('/api/pilot/experts/dram/wiki-refresh')
        self.assertEqual(expert.status_code,202)
        self.assertNotEqual(expert.json()['job_id'],common.json()['job_id'])
        self.assertEqual(self.client.post('/api/pilot/experts/missing/wiki-refresh').status_code,404)
        with connect(self.db) as db:
            db.execute("INSERT INTO expert_deletions VALUES('nor',?)",(now(),));db.commit()
        self.assertEqual(self.client.post('/api/pilot/experts/nor/wiki-refresh').status_code,404)
        status_url='/api/pilot/wiki-maintenance/jobs/'+str(expert.json()['job_id'])
        self.assertEqual(self.client.get(status_url).json()['expert_id'],'dram')
        self.create(role='superuser')
        for identity in ('3000000','4000000'):
            with self.sso(identity):
                for path in ('/api/pilot/wiki-maintenance/common','/api/pilot/experts/dram/wiki-refresh','/api/pilot/wiki-maintenance/retry'):
                    self.assertEqual(self.client.post(path,headers={'Origin':'http://testserver'}).status_code,403)
                self.assertEqual(self.client.get(status_url).status_code,403)
        with connect(self.db) as db:
            jobs=[tuple(r) for r in db.execute('SELECT scope,expert_id FROM wiki_jobs ORDER BY id')]
        self.assertEqual(jobs,[('common',''),('expert','dram')])

    def test_sso_auto_registration_is_persisted_visible_and_idempotent(self):
        # No administrator POST or signup precedes the first authenticated visit.
        with self.sso('4000001'):
            first=self.client.get('/api/auth/session')
            self.assertEqual(first.status_code,200)
            with connect(self.db) as db:
                row=db.execute("SELECT * FROM users WHERE employee_id='4000001'").fetchone()
                self.assertEqual((row['role'],row['name'],row['team']),('user','테스트 이름','테스트 팀'))
                self.assertEqual(row['profile_source'],'sso')
                created_at=row['created_at']
            for _ in range(3):
                self.assertEqual(self.client.get('/api/auth/session').status_code,200)
        listing=self.client.get('/api/users?q=4000001').json()
        self.assertEqual(listing['total'],1)
        user=listing['users'][0]
        self.assertEqual(user['employee_id'],'4000001')
        self.assertEqual(self.client.patch('/api/users/4000001',json={'role':'superuser','active':True,'revision':user['revision']}).status_code,200)
        with connect(self.db) as db:
            db.execute("UPDATE users SET profile_synced_at=NULL WHERE employee_id='4000001'");db.commit()
        with self.sso('4000001'),patch('backend.sso_adapter.fetch_employee_profile',return_value=EmployeeProfile('4000001','변경된 이름','변경된 팀')):
            returning=self.client.get('/api/auth/session').json()['user']
            self.assertEqual(returning['role'],'superuser')
            self.assertEqual(returning['team'],'변경된 팀')
            self.assertEqual(returning['created_at'],created_at)
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM users WHERE employee_id='4000001'").fetchone()[0],1)

    def test_profile_failure_does_not_create_incomplete_sso_account(self):
        with self.sso('4000002'),patch('backend.sso_adapter.fetch_employee_profile',side_effect=HTTPException(503,'directory unavailable')):
            self.assertEqual(self.client.get('/api/auth/session').status_code,503)
        with connect(self.db) as db:
            self.assertIsNone(db.execute("SELECT employee_id FROM users WHERE employee_id='4000002'").fetchone())
        with self.sso('4000002'):
            self.assertEqual(self.client.get('/api/auth/session').status_code,200)

    def test_seed_admin_retains_role_when_sso_profile_arrives_and_is_cached(self):
        with self.sso('9000001'):
            response=self.client.get('/api/auth/session');self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['user']['role'],'admin')
            with patch('backend.sso_adapter.fetch_employee_profile',side_effect=AssertionError('should use cached profile')):
                self.assertEqual(self.client.get('/api/users').status_code,200)

    def test_disabled_user_and_mismatched_profile_cannot_log_in(self):
        user=self.create()
        self.client.patch('/api/users/3000000',json={'role':'user','active':False,'revision':user['revision']})
        with self.sso('3000000'):
            self.assertEqual(self.client.get('/api/auth/session').status_code,403)
        with self.sso('5555555'),patch('backend.sso_adapter.fetch_employee_profile',return_value=EmployeeProfile('9000001','wrong','wrong')):
            self.assertEqual(self.client.get('/api/auth/session').status_code,503)
        with connect(self.db) as db:self.assertIsNone(db.execute("SELECT * FROM users WHERE employee_id='5555555'").fetchone())

    def test_cookie_authenticated_writes_require_same_origin(self):
        self.assertEqual(self.client.post('/api/users',json={'employee_id':'123'},headers={'Origin':'https://untrusted.invalid'}).status_code,403)
        with self.sso('9000001'):
            self.assertEqual(self.client.post('/api/users',json={'employee_id':'123'}).status_code,403)
            self.assertEqual(self.client.post('/api/users',json={'employee_id':'123'},headers={'Origin':'http://testserver'}).status_code,201)


if __name__=='__main__':unittest.main()
