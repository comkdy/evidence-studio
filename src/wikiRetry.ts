import { api } from './identity.ts';
import type { WikiJob } from './wikiUsage';

export type WikiRetryResult={status:'queued'|'running';job_id:number;already_requested:boolean};

export async function retryWikiJob(job:WikiJob):Promise<WikiRetryResult>{
 if(job.state!=='failed')throw new Error('실패한 Wiki 작업만 재시도할 수 있습니다.');
 return api<WikiRetryResult>('/api/pilot/wiki-maintenance/jobs/'+job.id+'/retry',{
  method:'POST',body:JSON.stringify({failed_at:job.finished_at}),
 });
}
