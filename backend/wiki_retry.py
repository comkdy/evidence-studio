"""Shared retry guards; snapshots cover all failures, not only visible cards."""
import hashlib
import json


def failure_snapshot(jobs):
    failures = sorted((job['id'], job['finished_at']) for job in jobs if job['state'] == 'failed')
    encoded = json.dumps(failures, separators=(',', ':')).encode('utf-8')
    return dict(failed_count=len(failures), snapshot=hashlib.sha256(encoded).hexdigest())


def expert_retryable(db, job):
    if job['scope'] != 'expert':
        return True
    return bool(db.execute("""SELECT e.id FROM expert_categories e WHERE e.id=?
        AND NOT EXISTS(SELECT 1 FROM expert_deletions d WHERE d.expert_id=e.id)
        AND NOT EXISTS(SELECT 1 FROM expert_policies p WHERE p.expert_id=e.id AND p.enabled=0)""",
        (job['expert_id'],)).fetchone())
