import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import * as yaml from 'js-yaml';
import approval from '../tools/adversarial_reviewer/approval.cjs';
import scope from '../tools/adversarial_reviewer/scope.cjs';
import publish from '../tools/adversarial_reviewer/publish.cjs';
import {applicable} from '../tools/adversarial_reviewer/codeowners.cjs';
import status from '../tools/adversarial_reviewer/status.cjs';

const head = 'a'.repeat(40);
const command = `/savior review skip ${head}`;
function fixture() {
  const comment = {id:42,body:command,user:{id:7,type:'User',login:'owner'},created_at:'now',updated_at:'now'};
  const pr = {number:5,state:'open',draft:false,user:{login:'owner',type:'User'},head:{sha:head},changed_files:1,
    base:{sha:'base',ref:'main',repo:{full_name:'org/repo'}}};
  const quote = {schema:3,projection_informational:true,next_call_reserved_nanodollars:3e9,
    id:'b'.repeat(64),binding:{repository:'org/repo',pr:5,head,base:'base'},blocked_reason:'cost_limit',projected_nanodollars:134e9,
    policy:{repository:'org/repo',pr:5,utc_day:'2026-11-12',pr_limit_nanodollars:2e9,daily_limit_nanodollars:5e9,pr_remaining_nanodollars:2e9,daily_remaining_nanodollars:5e9}};
  const artifact = {id:8,name:`review-cost-block-5-${head}-${quote.id}`,expired:false,size_in_bytes:100,workflow_run:{id:6}};
  const run = {repository:{full_name:'org/repo'},path:'.github/workflows/adversarial-review.yml',event:'workflow_dispatch',head_branch:'main'};
  const comments = [], reviews = [], created = [], refs = [], outputs = {}, summaries = [];
  const context = {repo:{owner:'org',repo:'repo'},runId:9,eventName:'issue_comment',payload:{action:'created',
    repository:{default_branch:'main'},issue:{number:5,pull_request:{}},comment:structuredClone(comment)}};
  const github = {rest:{
    pulls:{get:async()=>({data:structuredClone(pr)}),listReviews:'reviews',listFiles:'files',createReview:async r=>{
      created.push(r); reviews.push({...r,body:r.body,state:'APPROVED',user:{login:'github-actions[bot]',type:'Bot'}});
    }},
    repos:{getCollaboratorPermissionLevel:async()=>({data:{permission:'admin'}}),
      getContent:async({ref})=>{refs.push(ref);return {data:{type:'file',encoding:'base64',content:Buffer.from('* @owner').toString('base64')}};}},
    issues:{getComment:async()=>({data:structuredClone(comment)}),listComments:'comments',
      createComment:async c=>comments.push({...c,id:comments.length+1,user:{login:'github-actions[bot]',type:'Bot'}}),
      updateComment:async c=>Object.assign(comments.find(x=>x.id===c.comment_id),c)},
    actions:{listArtifactsForRepo:'artifacts',getWorkflowRun:async()=>({data:run}),downloadArtifact:async()=>({data:Buffer.from('archive')})}},
    paginate:async method=>structuredClone(method==='artifacts' ? [artifact] : method==='reviews' ? reviews : method==='files' ? [{filename:'src/a.js'}] : comments)};
  const core = {notice:()=>{},setOutput:(k,v)=>{outputs[k]=v;},setFailed:()=>{outputs.failed=true;},
    summary:{addRaw:s=>{summaries.push(s);return {write:async()=>{}};}}};
  return {github,context,pr,quote,comment,artifact,run,comments,refs,outputs,core,summaries,reviews,created,decode:()=>quote};
}

test('only skip with a full SHA is accepted; spending increases are removed',()=>{
  assert.deepEqual(approval.parse(command),{head});
  for (const body of [`/savior review approve ${'a'.repeat(64)} max-usd=99`, '/savior review skip abc',command+'\nextra','x'.repeat(10000)]) {
    assert.throws(()=>approval.parse(body),/invalid_approval_command/);
  }
});

test('admin author can waive recorded cost block once without inference',async()=>{
  const f=fixture();
  await approval.from_comment(f);
  await approval.from_comment(f);
  assert.equal(f.created.length,1);
  assert.equal(f.created[0].event,'APPROVE');
  assert.equal(f.created[0].commit_id,head);
  assert.match(f.created[0].body,/LGTM — automated review waived by @owner/);
  assert.match(f.created[0].body,/No automated code-review pass is asserted/);
  assert.match(f.created[0].body,/#issuecomment-42/);
  assert.match(f.created[0].body,/Next-call reservation: \$3.00/);
});

test('unauthorized, edited, deleted, stale and untrusted requests cannot approve',async()=>{
  const scenarios = [
    [f=>{f.context.payload.action='edited';},'invalid_approval_event'],
    [f=>{f.context.payload.comment.user.type='Bot';},'invalid_approval_event'],
    [f=>{f.comment.updated_at='later';},'approval_edited'],
    [f=>{f.comment.body+=' ';},'approval_edited'],
    [f=>{f.comment.user.id=99;},'approval_edited'],
    [f=>{f.github.rest.issues.getComment=async()=>{throw Object.assign(Error('missing'),{status:404});};},'approval_deleted'],
    ...['write','maintain','read'].map(permission=>[f=>{f.github.rest.repos.getCollaboratorPermissionLevel=async()=>({data:{permission}});},'approval_unauthorized']),
    [f=>{f.pr.draft=true;},'pr_not_ready'],[f=>{f.pr.state='closed';},'pr_not_ready'],
    [f=>{f.pr.base.ref='untrusted-owners';},'pr_not_ready'],
    [f=>{f.pr.head.sha='c'.repeat(40);},'quote_stale'],[f=>{f.pr.base.sha='new';},'quote_stale'],
    [f=>{f.quote.next_call_reserved_nanodollars=2e9;},'quote_stale'],
    [f=>{delete f.quote.next_call_reserved_nanodollars;},'quote_stale'],
    [f=>{f.quote.schema=2;},'quote_stale'],
    [f=>{f.quote.blocked_reason='spending_unresolved';},'quote_stale'],
    [f=>{f.artifact.expired=true;},'quote_unavailable'],[f=>{f.artifact.size_in_bytes=1_000_001;},'quote_unavailable'],
    [f=>{f.run.event='pull_request';},'untrusted_quote'],[f=>{f.run.head_branch='feature/untrusted';},'untrusted_quote'],
    [f=>{f.run.repository.full_name='attacker/repo';},'untrusted_quote'],
    [f=>{f.run.path='.github/workflows/other.yml';},'untrusted_quote'],
    [f=>{f.decode=()=>{throw Error('expired');};},'quote_expired_or_invalid'],
  ];
  for (const [mutate,reason] of scenarios) {
    const f=fixture();mutate(f);
    await assert.rejects(approval.from_comment(f),new RegExp(reason));
    assert.equal(f.created.length,0);
  }
});

test('permissions, head, base, comment and requests for changes are rechecked before approval',async()=>{
  for (const mutate of [p=>{p.head.sha='changed';},p=>{p.base.sha='changed';},p=>{p.draft=true;},p=>{p.state='closed';}]) {
    const f=fixture();let reads=0;
    f.github.rest.pulls.get=async()=>{const p=structuredClone(f.pr);if (++reads===2) mutate(p);return {data:p};};
    await assert.rejects(approval.from_comment(f));
    assert.equal(f.created.length,0);
  }
  const f=fixture();let checks=0;
  f.github.rest.repos.getCollaboratorPermissionLevel=async()=>({data:{permission:++checks===3 ? 'write':'admin'}});
  await assert.rejects(approval.from_comment(f),/approval_unauthorized/);
  const edited=fixture();let reads=0;
  edited.github.rest.issues.getComment=async()=>({data:{...edited.comment,updated_at:++reads===3 ? 'later':'now'}});
  await assert.rejects(approval.from_comment(edited),/approval_edited/);
  for (const commit of [head,'old']) {
    const blocked=fixture();
    blocked.reviews.push({commit_id:commit,state:'CHANGES_REQUESTED',user:{login:'reviewer',type:'User'}});
    await assert.rejects(approval.from_comment(blocked),/changes_requested/);
    blocked.reviews.push({commit_id:commit,state:'DISMISSED',user:{login:'reviewer',type:'User'}});
    await approval.from_comment(blocked);
    assert.equal(blocked.created.length,1);
  }
});

test('large PRs have no approval shortcut and bad comments cannot replace status',async()=>{
  for (const enabled of ['false','true']) {
    const f=fixture();f.context.eventName='pull_request_target';f.context.payload.pull_request=f.pr;
    f.pr.changed_files=1000;f.pr.additions=100000;f.pr.deletions=100000;
    await scope({...f,enabled});
    assert.equal(f.outputs.eligible,enabled);
    assert.equal(f.created.length,0);
  }
  const f=fixture();f.github.rest.repos.getCollaboratorPermissionLevel=async()=>({data:{permission:'write'}});
  await scope({...f,enabled:'true'});
  assert.equal(f.outputs.eligible,'false');assert.equal(f.comments.length,0);
  assert.match(f.summaries[0],/only repository admins/);
});

test('cost status is compact, notifies applicable owners once per revision and never submits a defect',async()=>{
  const f=fixture();
  const report={commit_id:head,blocked_reason:'cost_limit',quote:f.quote};
  await publish({...f,head,pr:5,phase:'complete',report});
  await publish({...f,head,pr:5,phase:'complete',report});
  assert.equal(f.comments.length,2);
  assert.match(f.comments[0].body,/Next-call reservation: \$3.00/);
  assert.match(f.comments[0].body,/Whole-plan worst-case projection \(informational\): \$134.00/);
  assert.match(f.comments[0].body,/of \$2.00/);assert.match(f.comments[0].body,/of \$5.00/);
  assert.doesNotMatch(f.comments[0].body,/new spend|max-usd|pilot|Verdict/);
  assert.match(f.comments[1].body,/@owner: automated review is blocked/);
  assert.deepEqual(f.refs,['base']);assert.equal(f.created.length,0);
  assert.ok(f.comments[0].body.length<1500);
});

test('CODEOWNERS applies last match, teams, exclusions, nested paths and renamed files',()=>{
  const source='* @all\n/src/ @org/team\n*.js @js\n/src/private/\n/docs/ @docs # comment\n';
  assert.deepEqual(applicable(source,['src/a.c','src/a.js','src/private/key.js','docs/a.md']),['@docs','@js','@org/team']);
  assert.deepEqual(applicable(source,['src/private/key.js']),[]);
  assert.deepEqual(applicable('/root @root\nfolder/ @nested\n',['deep/folder/a','deep/root']),['@nested']);
  assert.deepEqual(applicable('docs/* @docs\n',['docs/nested/a.md']),[]);
  assert.deepEqual(applicable('**/logs/** @logs\n',['logs/a','a/logs/b']),['@logs']);
  assert.deepEqual(applicable('* @all\n*.js @js\n',['.github/test.js']),['@js']);
});

test('cost display rounds reservations up and available balances down',()=>{
  const {quote}=fixture();quote.next_call_reserved_nanodollars=2_000_000_001;
  quote.policy.daily_remaining_nanodollars=4_999_999_999;
  const body=status.render('cost_limit',quote);
  assert.match(body,/Next-call reservation: \$2.01/);
  assert.match(body,/Daily allowance remaining: \$4.99/);
});

test('workflow serializes waivers and spending, keeps trusted checkouts, and gates AWS after offline projection',()=>{
  const workflow=yaml.load(fs.readFileSync(new URL('../.github/workflows/adversarial-review.yml',import.meta.url),'utf8'));
  assert.deepEqual(workflow.on.issue_comment.types,['created']);
  assert.equal(workflow.concurrency.group,'bedrock-review-spending');
  assert.equal(workflow.concurrency['cancel-in-progress'],false);
  assert.equal(workflow.jobs.review.env.REVIEW_COST_POLICY,'true');
  const steps=workflow.jobs.review.steps,index=name=>steps.findIndex(s=>s.name===name);
  assert.ok(index('estimate and authorize spending without inference')<index('revalidate before inference'));
  assert.ok(index('revalidate before inference')<index('persist pilot reservation'));
  assert.ok(index('persist pilot reservation')<index('assume bedrock review role'));
  for (const name of ['revalidate before inference','persist pilot reservation','assume bedrock review role','qualify and assess']) {
    assert.equal(steps[index(name)].if,"steps.preflight.outputs.spend == 'true'");
  }
  assert.ok(!JSON.stringify(workflow).includes('REVIEW_BUDGET_GRANT'));
  for (const job of Object.values(workflow.jobs)) {
    const checkout=job.steps.find(s=>s.uses?.startsWith('actions/checkout@'));
    assert.equal(checkout.with.ref,'${{ github.event.pull_request.base.sha || github.sha }}');
    assert.equal(checkout.with['persist-credentials'],false);
  }
});
