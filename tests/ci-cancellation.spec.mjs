import {test} from 'node:test';
import assert from 'node:assert/strict';
import cancel from '../tools/ci/cancel_siblings.cjs';
const names = {'verify.yml':'verify', 'qualification.yml':'release qualification',
  'commit-messages.yml':'commit messages', 'adversarial-review.yml':'adversarial code review'};
const run = (id, file, extra={}) => ({id, path:'.github/workflows/'+file, name:names[file],
  head_sha:'head', repository:{full_name:'owner/repo'}, head_repository:{full_name:'owner/repo'},
  event:'pull_request', head_branch:'feature', status:'in_progress', run_attempt:1, ...extra});
const failed = run(1, 'verify.yml', {status:'completed', conclusion:'failure'});
async function exercise(runs, {trigger=failed, refresh={}, conflict=false, repository='owner/repo'}={}) {
  const cancelled = [];
  const github = {rest:{actions:{listWorkflowRunsForRepo:'runs',
    getWorkflowRun:async ({run_id})=>({data:refresh[run_id] || runs.find(r=>r.id===run_id) || trigger}),
    cancelWorkflowRun:async ({run_id})=>{if(conflict) throw Object.assign(Error('finished'),{status:409}); cancelled.push(run_id);}}},
    paginate:async ()=>runs};
  await cancel({github, context:{repo:{owner:'owner',repo:'repo'},
    payload:{repository:{full_name:repository},workflow_run:trigger}}, core:{notice(){}}});
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
