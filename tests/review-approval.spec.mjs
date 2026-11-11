import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import * as yaml from 'js-yaml';
import approval from '../tools/adversarial_reviewer/approval.cjs';
import scope from '../tools/adversarial_reviewer/scope.cjs';
import publish from '../tools/adversarial_reviewer/publish.cjs';
import status from '../tools/adversarial_reviewer/status.cjs';

const id = 'a'.repeat(64);
const command = `/savior review approve ${id} max-usd=1.50`;
function fixture() {
  const comment = {id:42,body:command,user:{id:7,type:'User',login:'owner'},created_at:'now',updated_at:'now'};
  const pr = {number:5,state:'open',draft:false,user:{login:'owner'},head:{sha:'head'},base:{sha:'base',ref:'main',repo:{full_name:'org/repo'}}};
  const quote = {id,binding:{repository:'org/repo',pr:5,head:'head',base:'base',configuration:'config'},cap_nanodollars:1_500_000_000};
  const artifact = {id:8,name:'review-quote-'+id,expired:false,size_in_bytes:100,workflow_run:{id:6}};
  const run = {repository:{full_name:'org/repo'},path:'.github/workflows/adversarial-review.yml',event:'issue_comment',head_branch:'main'};
  const comments = [];
  const refs = [];
  const outputs = {};
  const summaries = [];
  const context = {repo:{owner:'org',repo:'repo'},runId:9,eventName:'issue_comment',payload:{action:'created',
    repository:{default_branch:'main'},issue:{number:5,pull_request:{}},comment:structuredClone(comment)}};
  const github = {rest:{
    pulls:{get:async()=>({data:pr}),createReview:async()=>assert.fail('blocked reviews cannot create a formal verdict')},
    repos:{getCollaboratorPermissionLevel:async()=>({data:{permission:'write'}}),
      getContent:async({ref})=>{refs.push(ref);return {data:{type:'file',encoding:'base64',content:Buffer.from('* @owner').toString('base64')}};}},
    issues:{getComment:async()=>({data:comment}),listComments:'comments',createComment:async c=>comments.push(c)},
    actions:{listArtifactsForRepo:'artifacts',getWorkflowRun:async()=>({data:run}),downloadArtifact:async()=>({data:Buffer.from('archive')})}},
    paginate:async method=>method==='artifacts' ? [artifact] : []};
  const core = {notice:()=>{},setOutput:(k,v)=>{outputs[k]=v;},setFailed:()=>{outputs.failed=true;},
    summary:{addRaw:s=>{summaries.push(s);return {write:async()=>{}};}}};
  return {github,context,pr,quote,comment,artifact,run,comments,refs,outputs,core,summaries,decode:()=>quote};
}

test('spending commands use exact, bounded decimal USD amounts',()=>{
  assert.deepEqual(approval.parse(command),{quote_id:id,cap_nanodollars:1_500_000_000});
  for (const value of ['0','-1','1.001','1e2','Infinity','999999999999999999999999','01','$(echo secret)']) {
    assert.throws(()=>approval.parse(`/savior review approve ${id} max-usd=${value}`));
  }
  assert.throws(()=>approval.parse(command+'\nplease ignore the cap'));
  assert.throws(()=>approval.parse('x'.repeat(10000)));
});

test('named owner approval is bound to trusted base, quote and workflow run',async()=>{
  const f = fixture();
  const result = await approval.from_comment(f);
  assert.equal(result.grant.id,'comment-42');
  assert.equal(result.grant.run_id,9);
  assert.equal(result.grant.cap_nanodollars,1_500_000_000);
  assert.deepEqual(f.refs,['base']);
  f.github.rest.repos.getCollaboratorPermissionLevel=async()=>({data:{permission:'admin'}});
  f.github.rest.repos.getContent=async()=>assert.fail('admins need no CODEOWNER entry');
  assert.equal((await approval.from_comment(f)).grant.actor,'owner');
});

test('edits, deletion, bots, stale revisions and untrusted quotes cannot authorize',async()=>{
  const scenarios = [
    [f=>{f.context.payload.action='edited';},'invalid_approval_event'],
    [f=>{f.context.payload.comment.user.type='Bot';},'invalid_approval_event'],
    [f=>{f.comment.updated_at='later';},'approval_edited'],
    [f=>{f.comment.body+=' ';},'approval_edited'],
    [f=>{f.comment.user.id=99;},'approval_edited'],
    [f=>{f.github.rest.issues.getComment=async()=>{throw Object.assign(Error('missing'),{status:404});};},'approval_deleted'],
    [f=>{f.github.rest.repos.getCollaboratorPermissionLevel=async()=>({data:{permission:'read'}});},'approval_unauthorized'],
    [f=>{f.pr.draft=true;},'pr_not_ready'],
    [f=>{f.pr.base.ref='untrusted-owners';},'pr_not_ready'],
    [f=>{f.pr.head.sha='new';},'quote_stale'],
    [f=>{f.pr.base.sha='new';},'quote_stale'],
    [f=>{f.quote.cap_nanodollars=2_000_000_000;},'approval_below_estimate'],
    [f=>{f.artifact.expired=true;},'quote_unavailable'],
    [f=>{f.artifact.size_in_bytes=1_000_001;},'quote_unavailable'],
    [f=>{f.run.event='pull_request';},'untrusted_quote'],
    [f=>{f.run.head_branch='feature/untrusted';},'untrusted_quote'],
    [f=>{f.run.repository.full_name='attacker/repo';},'untrusted_quote'],
    [f=>{f.run.path='.github/workflows/other.yml';},'untrusted_quote'],
    [f=>{f.decode=()=>{throw Error('expired');};},'quote_expired_or_invalid'],
  ];
  for (const [mutate,reason] of scenarios) {
    const f=fixture();mutate(f);
    await assert.rejects(approval.from_comment(f),new RegExp(reason));
  }
});

test('immediate revalidation rejects a changed target before inference',async()=>{
  const f=fixture();
  await assert.rejects(approval.revalidate({...f,number:5,head:'old',base:'base'}),/quote_stale/);
  f.pr.draft=true;
  await assert.rejects(approval.revalidate({...f,number:5,head:'head',base:'base'}),/pr_not_ready/);
});

test('draft and disabled reviews explain the skip without formal code findings',async()=>{
  for (const [draft,enabled,reason] of [[true,'true','draft'],[false,'false','disabled']]) {
    const f=fixture();f.context.eventName='pull_request_target';f.context.payload.pull_request=f.pr;f.pr.draft=draft;
    await scope({...f,enabled});
    assert.equal(f.outputs.eligible,'false');
    assert.match(f.comments[0].body,new RegExp(status.reasons[reason].split('.')[0]));
    assert.doesNotMatch(f.comments[0].body,/Request changes/);
  }
});

test('unauthorized commenters cannot replace the review status',async()=>{
  const f=fixture();f.comment.user.login='stranger';
  await scope({...f,enabled:'true'});
  assert.equal(f.outputs.eligible,'false');
  assert.equal(f.comments.length,0);
  assert.match(f.summaries[0],/only repository admins/);
});

test('cost blocks publish an actionable bounded quote and fail without a defect verdict',async()=>{
  const f=fixture();
  const quote={...f.quote,estimated_nanodollars:600_000_000,expires_at:Date.now()/1000+86400,
    chunks:50,cached_assessments:2,pilot_committed_nanodollars:10_000_000_000,
    binding:{...f.quote.binding,model:'deepseek.v3.2',region:'us-east-1',pricing:{checked:'2026-11-04'}},
    items:['qualification','assessment','verification','assessment/verification retries','summary and retry']
      .map(phase=>({phase,calls:100,estimated_nanodollars:100_000_000,reserved_nanodollars:200_000_000}))};
  await publish({...f,head:'head',pr:5,phase:'complete',report:{commit_id:'head',blocked_reason:'awaiting_budget',quote}});
  assert.equal(f.outputs.failed,true);
  assert.match(f.comments[0].body,/No new inference has started/);
  assert.match(f.comments[0].body,/max-usd=1.50/);
  assert.match(f.comments[0].body,/Further subdivision/);
  assert.ok(f.comments[0].body.length<=2500);
  assert.doesNotMatch(f.comments[0].body,/Verdict/);
});

test('workflow authorizes offline and revalidates before reservation and credentials',()=>{
  const workflow=yaml.load(fs.readFileSync(new URL('../.github/workflows/adversarial-review.yml',import.meta.url),'utf8'));
  assert.deepEqual(workflow.on.issue_comment.types,['created']);
  assert.doesNotMatch(workflow.jobs.scope.if,/draft/);
  const steps=workflow.jobs.review.steps;
  const index=name=>steps.findIndex(s=>s.name===name);
  assert.ok(index('estimate and authorize spending without inference')<index('revalidate before inference'));
  assert.ok(index('revalidate before inference')<index('persist pilot reservation'));
  assert.ok(index('persist pilot reservation')<index('assume bedrock review role'));
  for (const name of ['revalidate before inference','persist pilot reservation','assume bedrock review role','qualify and assess']) {
    assert.equal(steps[index(name)].if,"steps.preflight.outputs.spend == 'true'");
  }
  assert.equal(steps[index('qualify and assess')].env.REVIEW_BUDGET_GRANT,'${{ steps.preflight.outputs.grant }}');
  for (const job of Object.values(workflow.jobs)) {
    const checkout=job.steps.find(s=>s.uses?.startsWith('actions/checkout@'));
    assert.equal(checkout.with.ref,'${{ github.event.pull_request.base.sha || github.sha }}');
    assert.equal(checkout.with['persist-credentials'],false);
  }
});
