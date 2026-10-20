import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.task_queue import ensure_schema
from backend.throughput_report import report, distribution


class ThroughputReportTests(unittest.TestCase):
    def test_empty_distribution_has_no_invented_latency(self):
        self.assertEqual(distribution([]), {'count':0,'p50':None,'p95':None})
        self.assertEqual(distribution([10,30])['p50'], 20)

    def test_report_is_read_only_aggregates_failures_and_respects_offset(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'metrics.sqlite3'
            with closing(sqlite3.connect(path)) as db:
                ensure_schema(db)
                db.execute('CREATE TABLE research_requests(id TEXT,question TEXT,cache_hit INT,status TEXT,job_id TEXT,result_json TEXT)')
                db.execute('CREATE TABLE research_request_metrics(request_id TEXT,initial_ms REAL,final_ms REAL,created_at TEXT)')
                for i,status,cache,initial,final in [(1,'completed',0,12,120),(2,'completed',1,8,8),(3,'failed',0,9,None)]:
                    db.execute('INSERT INTO research_requests VALUES(?,?,?,?,?,?)',(str(i),'private-question-do-not-export',cache,status,'job-'+str(i),json.dumps({'research':{'validation_status':'model_reviewed'}})))
                    db.execute('INSERT INTO research_request_metrics VALUES(?,?,?,?)',(str(i),initial,final,'2026-10-19T00:00:00+00:00'))
                db.execute('INSERT INTO model_call_metrics VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('m1','job-1','answer','test','shared',10,100,10,10,'{}','completed',1792368000))
                db.commit()
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            result=report(path,since='2026-10-19T09:00:00+09:00')
            self.assertEqual(result['requests'],3)
            self.assertEqual(result['states']['failed'],1)
            self.assertEqual(result['final_answer_ms']['count'],2)
            self.assertEqual(result['initial_evidence_ms']['p50'],9)
            self.assertEqual(result['uncached_final_answer_ms']['p50'],120)
            self.assertNotIn('private-question',json.dumps(result))
            self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(report(path,since='2026-10-19T09:00:01+09:00')['requests'],0)


if __name__=='__main__':unittest.main()
