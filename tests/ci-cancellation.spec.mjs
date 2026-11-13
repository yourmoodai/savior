import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import cancel from '../tools/ci/cancel_siblings.cjs';
const names = {'verify.yml':'verify', 'qualification.yml':'release qualification',
  'commit-messages.yml':'commit messages', 'adversarial-review.yml':'adversarial code review'};
const run = (id, file, extra={}) => ({id, path:'.github/workflows/'+file, name:names[file],
  head_sha:'head', repository:{full_name:'owner/repo'}, head_repository:{full_name:'owner/repo'},
  event:'pull_request', head_branch:'feature', status:'in_progress', run_attempt:1, ...extra});
const failed = run(1, 'verify.yml', {status:'completed', conclusion:'failure'});
async function exercise(runs, {trigger=failed, refresh={}, conflict=false, repository='owner/repo',
  direct=false, attempt=1, jobs=[]}={}) {
  const cancelled = [];
  const github = {rest:{actions:{listWorkflowRunsForRepo:'runs', listJobsForWorkflowRunAttempt:'jobs',
    getWorkflowRun:async ({run_id})=>({data:typeof refresh[run_id] === 'function' ? refresh[run_id]() : refresh[run_id] || runs.find(r=>r.id===run_id) || trigger}),
    cancelWorkflowRun:async ({run_id})=>{if(conflict) throw Object.assign(Error('finished'),{status:409}); cancelled.push(run_id);}}},
    paginate:async (endpoint, args)=>{
      if (endpoint === 'jobs') {
        assert.equal(args.run_id, trigger.id);
        assert.equal(args.attempt_number, attempt);
        return jobs;
      }
      assert.equal(args.head_sha, trigger.head_sha);
      return runs;
    }};
  await (direct ? cancel.from_failed_commit_check : cancel)({github, run_attempt:attempt,
    context:{runId:trigger.id, repo:{owner:'owner',repo:'repo'},
    payload:{repository:{full_name:repository},...(direct ? {} : {workflow_run:trigger})}}, core:{notice(){}}});
  return cancelled;
}
test('failure cancels only eligible siblings at the same revision', async()=>{
  assert.deepEqual(await exercise([failed, run(2,'qualification.yml'), run(3,'adversarial-review.yml'),
    run(4,'verify.yml',{head_sha:'other'}), run(5,'release.yml'),
    run(6,'verify.yml',{head_repository:{full_name:'fork/repo'}}),
    run(7,'verify.yml',{event:'push',head_branch:'v1.0'}),
    run(8,'verify.yml',{name:'forged name'}),
    run(9,'adversarial-review.yml',{event:'pull_request_target'})]), [2,3]);
});
test('successful, skipped and cancelled events do not cascade', async()=>{
  for (const conclusion of ['success','skipped','cancelled']) {
    assert.deepEqual(await exercise([run(2,'qualification.yml')],{trigger:{...failed,conclusion}}),[]);
  }
});
test('untrusted repository and unexpected workflows cannot trigger cancellation', async()=>{
  const runs = [run(2,'qualification.yml')];
  assert.deepEqual(await exercise(runs,{repository:'fork/repo'}),[]);
  assert.deepEqual(await exercise(runs,{trigger:{...failed,repository:{full_name:'fork/repo'}}}),[]);
  assert.deepEqual(await exercise(runs,{trigger:{...failed,path:'.github/workflows/release.yml'}}),[]);
  assert.deepEqual(await exercise(runs,{trigger:{...failed,event:'pull_request_target'}}),[]);
});
test('recovered or superseded attempts cannot cancel current work', async()=>{
  for (const latest of [{...failed,run_attempt:2}, {...failed,conclusion:'success'}, {...failed,status:'in_progress'}]) {
    assert.deepEqual(await exercise([run(2,'qualification.yml')],{refresh:{1:latest}}),[]);
  }
});
test('completion races and conflicts are harmless', async()=>{
  const runs = [failed,run(2,'qualification.yml')];
  assert.deepEqual(await exercise(runs,{refresh:{2:{...runs[1],status:'completed'}}}),[]);
  assert.deepEqual(await exercise(runs,{refresh:{2:{...runs[1],head_sha:'newer'}}}),[]);
  assert.deepEqual(await exercise(runs,{conflict:true}),[]);
});

const checking = run(1, 'commit-messages.yml');
const failed_job = {name:'check', run_id:1, run_attempt:1, head_sha:'head', status:'completed', conclusion:'failure'};
test('commit failure cancels siblings before its workflow completes, excluding itself', async()=>{
  assert.deepEqual(await exercise([checking, run(2,'verify.yml'), run(3,'qualification.yml',{status:'queued'}),
    run(4,'verify.yml',{head_sha:'newer'}), run(5,'release.yml')],
  {direct:true, trigger:checking, jobs:[failed_job]}), [2,3]);
});
test('direct cancellation requires the failed check from this exact run attempt', async()=>{
  for (const job of [null, {...failed_job, conclusion:'success'}, {...failed_job, conclusion:'cancelled'},
    {...failed_job, status:'in_progress'}, {...failed_job, run_attempt:2}, {...failed_job, run_id:9},
    {...failed_job, head_sha:'other'}, {...failed_job, name:'unrelated'}]) {
    assert.deepEqual(await exercise([run(2,'verify.yml')],
      {direct:true, trigger:checking, jobs:job ? [job] : []}), []);
  }
  for (const trigger of [failed, {...checking,head_repository:{full_name:'fork/repo'}},
    {...checking,event:'pull_request_target'}, {...checking,run_attempt:2}]) {
    assert.deepEqual(await exercise([run(2,'verify.yml')],
      {direct:true, trigger, jobs:[failed_job]}), []);
  }
});
test('a rerun starting during cancellation protects the remaining siblings', async()=>{
  let reads = 0;
  assert.deepEqual(await exercise([run(2,'verify.yml'),run(3,'qualification.yml')],
    {direct:true, trigger:checking, jobs:[failed_job],
      refresh:{1:()=> ++reads < 4 ? checking : {...checking,run_attempt:2}}}), [2]);
});
test('reusable commit validation delegates failure to the PR coordinator', ()=>{
  const require = createRequire(import.meta.url);
  const workflow = require('js-yaml').load(readFileSync(new URL('../.github/workflows/commit-messages.yml',import.meta.url),'utf8'));
  assert.ok(Object.hasOwn(workflow.on,'workflow_call'));
  assert.equal(workflow.jobs['cancel-siblings'],undefined);
  assert.notEqual(workflow.permissions.actions,'write');
});
