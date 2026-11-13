import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
const require=createRequire(import.meta.url);
const {assert_approval,assert_target,execute}=require('../tools/ci/pr_validation.cjs');
const yaml=require('js-yaml');
const binding={pr:'21',head:'head',base:'base',run:42,attempt:'1'};
const approval={id:1,user:{login:'github-actions[bot]',type:'Bot'},state:'APPROVED',commit_id:'head',body:'<!-- savior-assessment:head:base:42:1 -->'};
const pr={state:'open',draft:false,head:{sha:'head'},base:{sha:'base',ref:'main'},merge_commit_sha:'merge'};
test('only the exact current adversarial assessment admits builds',()=>{
  assert_approval([approval],binding);
  for(const patch of [{state:'DISMISSED'},{state:'CHANGES_REQUESTED'},{commit_id:'old'},{body:'cost waiver'},
    {body:'<!-- savior-assessment:head:base:42:2 -->'},{user:{login:'someone',type:'User'}}])
    assert.throws(()=>assert_approval([{...approval,...patch}],binding));
  assert.throws(()=>assert_approval([approval,{...approval,id:2,state:'DISMISSED'}],binding));
  for(const patch of [{draft:true},{state:'closed'},{head:{sha:'new'}},{base:{sha:'new',ref:'main'}}])
    assert.throws(()=>assert_target({...pr,...patch},binding));
});
test('failed and skipped required validation never establish readiness',async()=>{
  for(const failure of ['review','admission','commits','verify','qualification','site']) {
    for(const state of ['failure','cancelled','skipped']) {
      const statuses=[];
      const results=Object.fromEntries(['review','admission','commits','verify','qualification','site'].map(k=>[k,{result:'success',outputs:{site_required:'true'}}]));
      results[failure].result=state;
      const github={rest:{pulls:{get:async()=>({data:pr}),listReviews:'reviews'},repos:{createCommitStatus:async s=>statuses.push(s)}},paginate:async()=>[approval]};
      await assert.rejects(execute({github,context:{repo:{owner:'owner',repo:'repo'},payload:{}},core:{},phase:'finish',binding,results}));
      assert.equal(statuses.at(-1).state,'failure');
    }
  }
});
test('PR builds have explicit admission dependencies and read-only tokens',()=>{
  const workflow=yaml.load(readFileSync('.github/workflows/pr-validation.yml','utf8'));
  for(const name of ['commits','verify','qualification','site']) {
    assert.ok(workflow.jobs[name].needs.includes('admission'));
    assert.ok(Object.values(workflow.jobs[name].permissions).every(p=>p==='read'));
  }
  for(const name of ['verify','qualification','site','commit-messages']) {
    const doc=yaml.load(readFileSync(`.github/workflows/${name}.yml`,'utf8'));
    assert.ok(!Object.hasOwn(doc.on,'pull_request'));
    assert.ok(Object.hasOwn(doc.on,'workflow_call'));
  }
});

test('admission verifies merge parents and detects a concurrent head change',async()=>{
  for(const scenario of ['valid','wrong_parent','head_changed']) {
    let reads=0; const outputs={};
    const github={rest:{pulls:{get:async()=>({data:scenario==='head_changed' && reads++>0 ? {...pr,head:{sha:'new'}} : pr}),listReviews:'reviews',listFiles:'files'},
      git:{getCommit:async()=>({data:{sha:'merge',parents:[{sha:'base'},{sha:scenario==='wrong_parent'?'other':'head'}]}})}},
      paginate:async api=>api==='reviews'?[approval]:[{filename:'site/templates/base.html'}]};
    const call=execute({github,context:{repo:{},payload:{}},core:{setOutput:(k,v)=>outputs[k]=v},phase:'admit',binding});
    if(scenario==='valid') {await call;assert.equal(outputs.test_sha,'merge');assert.equal(outputs.site_required,'true');}
    else {await assert.rejects(call);assert.equal(outputs.test_sha,undefined);}
  }
});

test('only an explicitly planned site skip can produce successful readiness',async()=>{
  for(const required of ['true','false']) {
    const statuses=[];
    const results=Object.fromEntries(['review','admission','commits','verify','qualification','site'].map(k=>[k,{result:'success',outputs:{site_required:required}}]));
    results.site.result='skipped';
    const github={rest:{pulls:{get:async()=>({data:pr}),listReviews:'reviews'},repos:{createCommitStatus:async s=>statuses.push(s)}},paginate:async()=>[approval]};
    const call=execute({github,context:{repo:{},payload:{}},core:{},phase:'finish',binding,results});
    if(required==='false') {await call;assert.ok(statuses.every(s=>s.state==='success'));}
    else await assert.rejects(call);
  }
});
