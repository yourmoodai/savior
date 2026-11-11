import test from 'node:test';
import assert from 'node:assert/strict';
import {approve, qualifies} from '../tools/adversarial_reviewer/large_admin.cjs';
import scope from '../tools/adversarial_reviewer/scope.cjs';

function fixture() {
  const pr = {number:5,state:'open',draft:false,user:{login:'maintainer',type:'User'},
    head:{sha:'head'},base:{sha:'base',ref:'main'},changed_files:100,additions:100,deletions:20};
  const created = [], summaries = [], failures = [], notices = [], outputs = {};
  const reviews = [];
  const context = {repo:{owner:'org',repo:'repo'},eventName:'pull_request_target',
    payload:{repository:{default_branch:'main'},pull_request:pr}};
  const github = {rest:{
    repos:{getCollaboratorPermissionLevel:async()=>({data:{permission:'admin'}}),
      getContent:async()=>({data:{type:'file',encoding:'base64',content:Buffer.from('* @other').toString('base64')}})},
    pulls:{get:async()=>({data:structuredClone(pr)}),listReviews:'reviews',
      createReview:async review=>{
        created.push(review);
        reviews.push({commit_id:review.commit_id,state:'APPROVED',user:{login:'github-actions[bot]',type:'Bot'}});
      }},
    issues:{listComments:'comments',createComment:async()=>{}}},
    paginate:async method=>method==='reviews' ? structuredClone(reviews) : []};
  const core = {notice:message=>notices.push(message),setFailed:message=>failures.push(message),
    setOutput:(name,value)=>{outputs[name]=value;},
    summary:{addRaw:body=>({write:async()=>{summaries.push(body);}})}};
  return {pr,github,context,core,created,summaries,failures,notices,outputs,reviews};
}

test('large PR thresholds include deletions and reject missing or invalid counts',()=>{
  const {pr} = fixture();
  for (const [files,additions,deletions,expected] of [
    [99,5000,4999,false],[100,0,0,true],[1,5000,5000,true],[1,0,10000,true],
    [1,9999,0,false],[NaN,0,0,false],[100,-1,0,false],[100,0,undefined,false],
  ]) assert.equal(qualifies({...pr,changed_files:files,additions,deletions},'main'),expected);
  for (const fields of [{draft:true},{state:'closed'},{base:{ref:'develop'}}]) {
    assert.equal(qualifies({...pr,...fields},'main'),false);
  }
});

test('large admin PR gets one current-head approval with inference disabled',async()=>{
  const f = fixture();
  await scope({...f,enabled:'false'});
  await scope({...f,enabled:'true'});
  assert.equal(f.created.length,1);
  assert.equal(f.created[0].event,'APPROVE');
  assert.equal(f.created[0].commit_id,'head');
  assert.match(f.created[0].body,/LGTM.*full assessment skipped/);
  assert.match(f.summaries[0],/No inference or spending reservation/);
  assert.equal(f.outputs.eligible,'false');
  assert.deepEqual(f.failures,[]);
});

test('normal admin and large non-admin PRs continue through normal review',async()=>{
  const normal = fixture();normal.pr.changed_files=99;
  await scope({...normal,enabled:'true'});
  assert.equal(normal.outputs.eligible,'true');
  assert.equal(normal.created.length,0);
  const owner = fixture();
  owner.github.rest.repos.getCollaboratorPermissionLevel=async()=>({data:{permission:'write'}});
  owner.github.rest.repos.getContent=async()=>({data:{type:'file',encoding:'base64',content:Buffer.from('* @maintainer').toString('base64')}});
  await scope({...owner,enabled:'true'});
  assert.equal(owner.outputs.eligible,'true');
  assert.equal(owner.created.length,0);
  const stranger = fixture();
  stranger.github.rest.repos.getCollaboratorPermissionLevel=async()=>({data:{permission:'write'}});
  await scope({...stranger,enabled:'true'});
  assert.equal(stranger.outputs.eligible,'false');
  assert.equal(stranger.created.length,0);
});

test('requests for changes block LGTM until superseded or dismissed',async()=>{
  for (const login of ['github-actions[bot]','human-reviewer']) {
    const f = fixture();
    f.reviews.push({commit_id:'head',state:'CHANGES_REQUESTED',user:{login,type:'User'}});
    await scope({...f,enabled:'false'});
    assert.equal(f.created.length,0);
    assert.equal(f.outputs.eligible,'false');
    assert.match(f.failures[0],/request for changes/);
    f.reviews.push({commit_id:'head',state:'DISMISSED',user:{login,type:'User'}});
    await scope({...f,enabled:'false'});
    assert.equal(f.created.length,1);
  }
  const f=fixture();
  f.reviews.push({commit_id:'old',state:'CHANGES_REQUESTED',user:{login:'human-reviewer',type:'User'}});
  await approve(f);
  assert.equal(f.created.length,1);
});

test('approval rechecks head, base, size, readiness, author and permissions',async()=>{
  for (const mutate of [
    p=>{p.head.sha='new';},p=>{p.base.sha='new';},p=>{p.draft=true;},
    p=>{p.state='closed';},p=>{p.base.ref='develop';},p=>{p.changed_files=1;},
    p=>{p.user.login='other';},p=>{p.user.type='Bot';},
  ]) {
    const f = fixture();
    let reads=0;
    f.github.rest.pulls.get=async()=>{
      const pr=structuredClone(f.pr);
      if (++reads===2) mutate(pr);
      return {data:pr};
    };
    await approve(f);
    assert.equal(f.created.length,0);
  }
  for (const permission of ['write','maintain','read']) {
    const f=fixture();let checks=0;
    f.github.rest.repos.getCollaboratorPermissionLevel=async()=>({data:{permission:++checks===3 ? permission : 'admin'}});
    await approve(f);
    assert.equal(f.created.length,0);
  }
});

test('API errors, draft PRs and spending comments cannot create LGTM approvals',async()=>{
  for (const method of ['permission','pr','reviews']) {
    const f=fixture();
    const fail=async()=>{throw Error('unavailable');};
    if (method==='permission') f.github.rest.repos.getCollaboratorPermissionLevel=fail;
    else if (method==='pr') {
      let reads=0;
      f.github.rest.pulls.get=async()=>{if (++reads>1) return fail();return {data:f.pr};};
    } else f.github.paginate=async name=>name==='reviews' ? fail() : [];
    if (method==='pr') await assert.rejects(scope({...f,enabled:'true'}),/unavailable/);
    else await scope({...f,enabled:'true'});
    assert.equal(f.outputs.eligible,'false');
    assert.equal(f.created.length,0);
  }
  const draft=fixture();draft.pr.draft=true;
  await scope({...draft,enabled:'false'});
  assert.equal(draft.created.length,0);
  const comment=fixture();
  comment.context.eventName='issue_comment';
  await scope({...comment,enabled:'true'});
  assert.equal(comment.outputs.eligible,'false');
  assert.equal(comment.created.length,0);
});
