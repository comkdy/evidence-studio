import tempfile
from datetime import datetime,timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from backend.pilot_db import connect,initialize
from backend.task_queue import enqueue_task,register_handler,run_next
from backend import wiki_lint


class ScheduledLintTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database=Path(self.temp.name)/'lint.sqlite3'
        initialize(self.database)
        self.model=patch('backend.wiki_lint.CompanyLLM')
        self.mock_model=self.model.start()
        self.addCleanup(self.model.stop)
        self.mock_model.return_value.configured=False
        wiki_lint.register_handlers()

    def test_schedule_deduplicates_and_respects_daily_interval(self):
        with connect(self.database) as db:
            first=wiki_lint.enqueue_scheduled_work(db)
            self.assertTrue(first)
            self.assertIsNone(wiki_lint.enqueue_scheduled_work(db))
            self.assertEqual(db.execute('SELECT priority FROM work_tasks WHERE id=?',(first,)).fetchone()[0],60)
            db.execute("UPDATE work_tasks SET state='completed' WHERE id=?",(first,))
            from backend.terminology import snapshot
            from backend.wiki_policy import load_prompt
            model='offline:rules:'+load_prompt('lint').fingerprint+':terms:'+str(snapshot(self.database)['revision'])
            stamp=datetime.now(timezone.utc).isoformat()
            db.execute('INSERT INTO wiki_lint_runs VALUES(?,?,?,?,?,?,?,?,?)',('review',stamp,stamp,'completed',model,'','report','[]',1))
            db.commit()
            self.assertIsNone(wiki_lint.enqueue_scheduled_work(db))

    def test_each_call_yields_to_user_and_chain_obeys_budget(self):
        order=[]
        def lint(database,**kwargs):
            self.assertEqual(kwargs['max_calls'],1)
            order.append('lint')
            return {'status':'partial','llm_calls':1,'review_chars':10,'model_failed':False}
        register_handler('test.foreground.lint',lambda p,c:order.append('answer') or {})
        with patch('backend.wiki_lint.MAX_LLM_CALLS',3),connect(self.database) as db:
            wiki_lint.enqueue_scheduled_work(db);db.commit()
        with patch('backend.wiki_lint.run_lint',side_effect=lint) as review:
            self.assertTrue(run_next(self.database))
            with connect(self.database) as db:
                enqueue_task(db,'test.foreground.lint','question','v1',priority=0);db.commit()
            while run_next(self.database):pass
            self.assertEqual(review.call_count,3)
            self.assertEqual(order,['lint','answer','lint','lint'])
            self.assertEqual([c.kwargs['max_review_chars'] for c in review.call_args_list],[60000,59990,59980])

    def test_model_failure_does_not_schedule_more_calls(self):
        with connect(self.database) as db:
            wiki_lint.enqueue_scheduled_work(db);db.commit()
        with patch('backend.wiki_lint.run_lint',return_value={'status':'partial','llm_calls':1,'review_chars':10,'model_failed':True}) as review:
            self.assertTrue(run_next(self.database))
            self.assertFalse(run_next(self.database))
            self.assertEqual(review.call_count,1)
