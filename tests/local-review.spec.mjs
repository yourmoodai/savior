import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtempSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {snapshot} from '../tools/local_review/source.mjs';
import {assess} from '../tools/local_review/assess.mjs';
import {complete} from '../tools/local_review/run.mjs';
import {outstanding_findings} from '../tools/local_review/evidence.mjs';
import receipt from '../tools/local_review/receipt.cjs';
import validation from '../tools/ci/pr_validation.cjs';

const {repository,repo_id,app_id,check_name,receipt_id,validate_check,marker}=receipt;
const review_bot = {id:360859138, login:'yourmood-ai[bot]', type:'Bot'};
const pr={number:42,head:{sha:'a'.repeat(40)},base:{sha:'b'.repeat(40)}};
const artifact={id:8,name:'',workflow_run:{id:9,repository_id:repo_id},size_in_bytes:123};
const run={id:9,path:'.github/workflows/pr-validation.yml',event:'workflow_dispatch',
  head_branch:'main',head_sha:pr.base.sha,display_title:'pr validation #42',status:'completed',repository:{full_name:repository}};
const sha='c'.repeat(64);
const agent={name:'agy',identity:sha};
const source={paths:['add.js'],available:['add.js'],read:()=>'return a-b;',patch:'@@ -0,0 +1 @@\n+return a-b;'};
const claim={id:1,path:'add.js'};
const clean={verdict:'approve',coverage:[{path:'add.js',reason:'verified the return value'}],
  prior:[{id:1,status:'refuted',reason:'verified against supplied file'}],findings:[]};
const defect={verdict:'changes_requested',coverage:clean.coverage,prior:[{...clean.prior[0],status:'confirmed'}],
  findings:[{path:'add.js',prior_ids:[1],line:1,side:'new',evidence:'return a-b;',reason:'addition now subtracts'}]};

test('require complete source coverage, prior dispositions, and exact finding lines',async()=>{
  for (const result of [clean,defect]) assert.equal((await assess(source,[claim],()=>result)).verdict,result.verdict);
  for (const result of [{...clean,coverage:[]},{...clean,prior:[]},{...clean,prior:defect.prior},
    {...defect,findings:[{...defect.findings[0],line:2}]},{...defect,verdict:'approve'},{...clean,verdict:'incomplete'}])
    await assert.rejects(assess(source,[claim],()=>result));
  const two={...defect,prior:[...defect.prior,{...defect.prior[0],id:2}]};
  await assert.rejects(assess(source,[claim,{...claim,id:2}],()=>two),/unresolved prior finding/);
  let calls=0;
  await assess(source,[claim],input=>{
    if (++calls===1) return {request:[{path:'add.js',side:'old'}]};
    const evidence=JSON.parse(input.split('\n').at(-1));
    assert.deepEqual(evidence.extra,[{path:'add.js',side:'old',text:'1\treturn a-b;'}]);
    return defect;
  });
});

test('ground a finding only against exact evidence in the named revision',async()=>{
  const input={...source,read:()=> 'first\n    return a-b;\nlast'};
  const finding={...defect.findings[0],evidence:'    return a-b;'};
  const report={...defect,findings:[finding]};
  const result=await assess(input,[claim],()=>report);
  assert.deepEqual(result.findings,[{...finding,line:2}]);
  for (const text of ['first\nreturn a-b;','first\n    return a-b;\n    return a-b;'])
    await assert.rejects(assess({...input,read:()=>text},[claim],()=>report),/unverified finding location/);
});

test('context requests must add evidence and can follow transitive dependencies',async()=>{
  const dependencies=['1.js','2.js','3.js','4.js'];
  const input={...source,available:[...source.paths,...dependencies]};
  let round=0;
  const result=await assess(input,[claim],prompt=>{
    const evidence=JSON.parse(prompt.split('\n').at(-1));
    assert.deepEqual(evidence.extra.map(f=>f.path),dependencies.slice(0,round));
    if (round===dependencies.length) return clean;
    return {request:[{path:'add.js',side:'new'},{path:dependencies[round++],side:'new'}]};
  });
  assert.equal(result.verdict,'approve');
  await assert.rejects(assess(source,[claim],()=>({request:[{path:'add.js',side:'new'}]})),/already supplied/);
});

test('historical paths absent from both trees still require an explicit disposition',async()=>{
  const previous={...claim,path:'removed.js'};
  const input={...source,read:path=>path==='add.js' ? 'return a-b;' : null};
  const result={...clean,coverage:[...clean.coverage,{path:'removed.js',reason:'checked removal and the remaining patch'}]};
  await assess(input,[previous],prompt=>{
    const evidence=JSON.parse(prompt.split('\n').at(-1));
    assert.deepEqual(evidence.files[1],{path:'removed.js',side:'absent',text:null});
    return result;
  });
  await assert.rejects(assess(input,[previous],()=>({...result,prior:[]})),/incomplete review coverage/);
});

test('a surviving defect can cite a changed destination from a different group',async()=>{
  const changed=['add.js','b','c','d','e','f'];
  const input={...source,paths:changed,available:[...changed,'unchanged.js'],
    read:path=>path==='removed.js' ? null : 'return a-b;'};
  const original={...claim,path:'removed.js'};
  const review=(finding,tree=input)=>assess(tree,[original],prompt=>{
    const {scope,prior}=JSON.parse(prompt.split('\n').at(-1));
    const coverage=scope.map(path=>({path,reason:'assessed the complete patch'}));
    return prior.length ? {...defect,coverage,findings:[finding]} : {...clean,coverage,prior:[]};
  });
  const finding=defect.findings[0];
  assert.deepEqual((await review(finding)).findings,[finding]);
  for (const change of [{path:'unchanged.js'},{side:'old'},{prior_ids:[]},{evidence:'wrong line'}])
    await assert.rejects(review({...finding,...change}));
  await assert.rejects(review(finding,{...input,available:[...input.available,'removed.js'],read:source.read}));
});

function check_fixture() {
  return {id:44,app:{id:app_id},name:check_name,status:'completed',conclusion:'success',head_sha:pr.head.sha,
    external_id:receipt_id(pr,artifact.id,sha),html_url:'https://github.com/yourmoodai/savior/runs/44',
    output:{text:'verified report',summary:'one file reviewed'}};
}
test('cache on GitHub survives a retry and never revives dismissed approval',async()=>{
  let calls=0,check;
  const reviews=[],events=[];
  const args={pr,agent,evidence:{artifact},existing:[],checks:async()=>[],refresh:async()=>{},inspect:async()=>{calls++;return clean;},
    write:async()=>async(path,method,payload)=>{
      events.push(path);
      if (path==='/check-runs') {check={...check_fixture(),...payload};return check;}
      reviews.push({id:1,user:review_bot,state:'APPROVED',commit_id:pr.head.sha,body:payload.body});
    },reviews:async()=>reviews};
  await complete(args);
  await complete({...args,existing:[check]});
  assert.equal(calls,1);
  assert.deepEqual(events,['/check-runs','/pulls/42/reviews']);
  reviews[0].state='DISMISSED';
  await assert.rejects(complete({...args,existing:[check]}),/dismissed/);
  reviews.push({id:2,user:{login:'maintainer'},state:'CHANGES_REQUESTED'});
  await assert.rejects(complete({...args,existing:[check]}),/outstanding/);
  assert.equal(events.length,2);
});

function cost_archive() {
  const quote={schema:3,projection_informational:true,blocked_reason:'cost_limit',
    binding:{repository,pr:42,head:pr.head.sha,base:pr.base.sha},projected_nanodollars:100,next_call_reserved_nanodollars:50,
    policy:{repository,pr:42,pr_limit_nanodollars:2e9,daily_limit_nanodollars:5e9,
      pr_remaining_nanodollars:100,daily_remaining_nanodollars:100,pilot_remaining_nanodollars:25}};
  const raw=execFileSync('python3',['-c',`import io,json,sys,time,zipfile
sys.path.insert(0,'tools/adversarial_reviewer')
from quote import digest
q=json.load(sys.stdin); q['issued_at']=int(time.time()); q['expires_at']=q['issued_at']+86400; q['id']=digest(q)
out=io.BytesIO()
with zipfile.ZipFile(out,'w') as z: z.writestr('quote.json',json.dumps(q))
sys.stdout.buffer.write(q['id'].encode()+out.getvalue())`],{input:JSON.stringify(quote)});
  return {id:raw.subarray(0,64).toString(),zip:raw.subarray(64)};
}
test('CI binds the App identity, review, trusted cost artifact and both revisions',async()=>{
  const archive=cost_archive();
  const item={...artifact,name:'review-cost-block-42-'+pr.head.sha+'-'+archive.id};
  let check=check_fixture();
  const context={repo:{owner:'yourmoodai',repo:'savior'},eventName:'workflow_dispatch',ref:'refs/heads/main',payload:{repository:{id:repo_id}}};
  const github={rest:{checks:{get:async()=>({data:check})},actions:{getArtifact:async()=>({data:item}),
    getWorkflowRun:async()=>({data:run}),downloadArtifact:async()=>({data:archive.zip})}}};
  const args={github,context,pr,id:'44'};
  const stamp=await validate_check(args);
  const review={id:6,user:review_bot,state:'APPROVED',commit_id:pr.head.sha,body:stamp+'\nreport'};
  validation.assert_approval([review],{head:pr.head.sha},stamp);
  const old_login = {...review_bot, login:'your-mood-savior-prose-review[bot]'};
  validation.assert_approval([{...review,id:5,state:'CHANGES_REQUESTED',user:old_login},review],{head:pr.head.sha},stamp);
  for (const user of [{...review_bot,id:12},{...review_bot,type:'User'}]) {
    assert.throws(()=>validation.assert_approval([{...review,user}],{head:pr.head.sha},stamp));
  }
  assert.throws(()=>validation.assert_approval([{...review,user:{login:'unrelated',type:'Bot'}}],{head:pr.head.sha},stamp));
  for (const fields of [{id:45},{head_sha:pr.base.sha},{app:{id:19}},{conclusion:'failure'},
    {external_id:receipt_id({...pr,base:pr.head},8,sha)}]) {
    check={...check_fixture(),...fields};
    await assert.rejects(validate_check(args));
  }
  check=check_fixture();
  assert.equal(marker(check),stamp);
  await assert.rejects(validate_check({...args,context:{...context,ref:'refs/heads/feature'}}));
  item.name+='wrong';
  await assert.rejects(validate_check(args),/quote_stale/);
});

test('pull request budget stops originate on the matching main target',()=>{
  const pull={...pr,base:{...pr.base,ref:'main',repo:{id:repo_id}},head:{...pr.head,repo:{id:repo_id}}};
  const source={...run,event:'pull_request_target',head_sha:pr.head.sha,pull_requests:[pull]};
  assert.equal(receipt.cost_run(artifact,source,pr),pr.base.sha);
  for (const changed of [{...pull,number:43},{...pull,base:{...pull.base,ref:'feature'}}])
    assert.throws(()=>receipt.cost_run(artifact,{...source,pull_requests:[changed]},pr));
});

test('preflight-only reports have no findings only with matching preflight evidence',()=>{
  const program=`import io,json,sys,zipfile
sys.path.insert(0,'tools/local_review')
from report import claims
base={'commit_id':'head','quote':{'id':'quote'},'blocked_reason':'cost_limit','budget':{}}
for report,preflight,expected in [(base,base,[]),({**base,'findings':[1]},base,[1]),(base,{},None),({**base,'completed_chunks':2},base,None)]:
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('report.json',json.dumps(report)); z.writestr('preflight.json',json.dumps(preflight))
    try: result=claims(out.getvalue(),'head','quote')
    except ValueError: result=None
    assert result==expected`;
  execFileSync('python3',['-c',program]);
});

test('carry unresolved App findings to a later revision using the original report',()=>{
  const check={...check_fixture(),conclusion:'failure',output:{text:'```json\n'+JSON.stringify(defect)+'\n```'}};
  const review={user:review_bot,id:7,state:'CHANGES_REQUESTED',commit_id:pr.head.sha,body:marker(check)};
  const next={...pr,head:{sha:'d'.repeat(40)}};
  assert.equal(outstanding_findings(next,[review],()=>check)[0].details,defect.findings[0].reason);
  assert.throws(()=>outstanding_findings(next,[review],()=>({...check,app:{id:88}})));
  assert.deepEqual(outstanding_findings(next,[{...review,state:'DISMISSED'}],()=>check),[]);
});

test('a competing App failure blocks admission regardless of review order',()=>{
  const failed={...check_fixture(),conclusion:'failure'};
  assert.throws(()=>receipt.assert_no_conflict([failed],[],pr),/conflicting/);
  const dismissal={state:'DISMISSED',user:review_bot,commit_id:pr.head.sha,body:marker(failed)};
  receipt.assert_no_conflict([failed],[dismissal],pr);
  assert.throws(()=>receipt.assert_no_conflict([failed],[{...dismissal,commit_id:'obsolete'}],pr));
});

test('a request for changes from a deleted account stays outstanding',()=>{
  const review={id:7,user:null,state:'CHANGES_REQUESTED'};
  assert.throws(()=>validation.assert_approval([review],{}),/changes requested/);
});

test('snapshot refuses binary evidence on either side of a change',()=>{
  const original = process.cwd();
  const directory = mkdtempSync(join(tmpdir(), 'savior-binary-review-'));
  const git = (...args) => execFileSync('git', ['-c', 'user.name=Fixture', '-c',
    'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false', '-c',
    'core.hooksPath=.git/no-hooks', ...args], {cwd:directory, encoding:'utf8'}).trim();
  try {
    process.chdir(directory);
    git('init', '--quiet');
    function commit(content) {
      writeFileSync('input.dat', content);
      git('add', 'input.dat');
      git('commit', '--quiet', '-m', 'fixture');
      return {sha:git('rev-parse', 'HEAD')};
    }
    const before = commit('text before');
    const binary = commit(Buffer.from([65, 0, 66]));
    const after = commit('text after');
    for (const [base, head] of [[before, binary], [binary, after]]) {
      assert.throws(()=>snapshot({base, head}), /^Error: binary evidence: input\.dat$/);
    }
  } finally {
    process.chdir(original);
    rmSync(directory, {recursive:true, force:true});
  }
});
