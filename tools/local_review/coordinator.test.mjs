import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdtempSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {Worker} from 'node:worker_threads';
import {Reservations} from './budget.mjs';
import {author_route,reconcile} from './coordinator.mjs';
import {repo_id,bot} from './github.mjs';

const pr={number:1,base:{repo:{id:repo_id},ref:'main',sha:'a'.repeat(40)},
  head:{repo:{id:repo_id},sha:'b'.repeat(40)},draft:false,state:'open',user:{id:11}};

test('author consent is advisory and revision-specific',()=>{
  for (const status of ['declined','exhausted','unavailable','reviewed']) {
    const comment={user:pr.user,body:['/savior author-review',pr.base.sha,pr.head.sha,status].join(' ')};
    assert.match(author_route(pr,[comment]),status==='reviewed'?/independent review/:/human review/);
    assert.match(author_route(pr,[{...comment,user:{id:12}}]),/awaiting/);
    assert.match(author_route({...pr,base:{...pr.base,sha:'c'.repeat(40)}},[comment]),/awaiting/);
  }
});

test('observation, changed base and force-push never publish an approval',async()=>{
  for (const changed of [null,'base','head']) {
    const writes=[];
    let reads=0;
    async function api(path,method) {
      if(method) writes.push(method);
      if(path==='/pulls/1') return changed && ++reads>1 ? {...pr,[changed]:{...pr[changed],sha:'c'.repeat(40)}}:pr;
      return [];
    }
    await reconcile(api,1,()=>true,Boolean(changed));
    assert.deepEqual(writes,[]);
  }
});

test('canary commits only its own exact-head approval and reconciles stale reviews',async()=>{
  const writes=[];
  const api=async(path,method,body)=>{
    if(method){writes.push([path,method,body]);return {};}
    if(path==='/pulls/1')return pr;
    if(path.includes('/reviews?'))return [{id:99,state:'APPROVED',user:{login:bot,type:'Bot'},body:'old',commit_id:pr.head.sha}];
    if(path.includes('/check-runs?'))return {total_count:0,check_runs:[]};
    throw Error(path);
  };
  await reconcile(api,1,()=>true,true);
  assert.equal(writes[0][1],'PUT');
  assert.equal(writes[1][2].commit_id,pr.head.sha);
  assert.ok(writes[1][2].body.includes(pr.base.sha));
  assert.equal(writes[2][2].conclusion,'success');
});

test('unknown dispatch stays reserved across restarts and concurrent revisions',async t=>{
  const dir=mkdtempSync(join(tmpdir(),'savior-budget-'));
  t.after(()=>rmSync(dir,{recursive:true,force:true}));
  const db=join(dir,'ledger.sqlite'), ledger=new Reservations(db);
  ledger.reserve(repo_id,1,'revision-a',1_990_000);
  ledger.db.close();
  const script = 'import {workerData,parentPort} from "node:worker_threads";'+
    'const {Reservations}=await import(workerData.url);const b=new Reservations(workerData.db);'+
    'try { b.reserve(workerData.repo,1,workerData.id,10000);parentPort.postMessage(true) }'+
    'catch { parentPort.postMessage(false) } finally { b.db.close() }';
  const race=id=>new Promise((resolve,reject)=>{
    const w=new Worker(new URL('data:text/javascript,'+encodeURIComponent(script)),
      {workerData:{db,id,repo:repo_id,url:new URL('./budget.mjs',import.meta.url).href}});
    w.once('message',resolve);w.once('error',reject);
  });
  assert.equal((await Promise.all([race('b'),race('c')])).filter(Boolean).length,1);
  const restarted=new Reservations(db);
  assert.throws(()=>restarted.reserve(repo_id,1,'another',1));
  restarted.settle(repo_id,1,'revision-a',1_980_000,'receipt-a');
  restarted.settle(repo_id,1,'revision-a',1_980_000,'receipt-a');
  assert.throws(()=>restarted.settle(repo_id,1,'revision-a',0,'receipt-b'));
  restarted.reserve(repo_id,1,'revision-d',10_000);
  restarted.db.close();
});
