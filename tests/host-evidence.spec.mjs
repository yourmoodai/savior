import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {child} from '../scripts/bench/common.mjs';
import {hostStream, hostCases, requiredStages} from '../scripts/bench/host.mjs';
import {validateMatrix} from '../scripts/release/host-matrix.mjs';
import {environmentSample} from '../scripts/bench/performance.mjs';

const event = (stream, name, fields = {}) => stream.line(JSON.stringify({protocol:1,event:name,
  scenario:'missing_model',strategy:'fixed_phrase',elapsed_ms:1,...fields}));
const pinned_host = JSON.parse(readFileSync(new URL('../tools/host/package.json', import.meta.url))).dependencies['@anthropic-ai/claude-code'];
const completion = {host_version:pinned_host+' (Claude Code)',host_sha256:'a'.repeat(64),submission_to_provider_ms:20,
  samples:1,no_request_on_failure:true,injection:'rewrite_model_path',recovery:'retained_draft_retry'};

const prefix = stream => {
  for (const name of ['ready','pane','filter_ready','failure_submission']) event(stream,name);
};

test('host completion requires validated recovery boundaries and discards unknown fields', () => {
  const checkpoints = [], stream = hostStream('missing_model','fixed_phrase',e=>checkpoints.push(e));
  prefix(stream);
  event(stream,'failure_detected',{no_request_on_failure:true,private:'secret-canary'});
  event(stream,'draft_ready'); event(stream,'recovery_submission'); event(stream,'provider_received');
  event(stream,'completed',{...completion,private:'secret-canary'});
  const result = stream.finish({status:0,milliseconds:30});
  assert.equal(result.status,'passed');
  assert.equal(result.no_request_on_failure,true);
  assert.equal(result.evidence.recovery,'retained_draft_retry');
  assert.ok(!JSON.stringify({result,checkpoints}).includes('secret-canary'));
  assert.throws(()=>event(stream,'draft_ready'),/schema_mismatch/);
});

test('completion cannot bypass failure checks, draft observation, or event identity', () => {
  const stream = hostStream('missing_model','fixed_phrase');
  assert.throws(()=>event(stream,'completed',completion),/schema_mismatch/);
  assert.throws(()=>event(stream,'ready',{scenario:'worker_exit'}),/schema_mismatch/);
  assert.throws(()=>event(stream,'ready',{elapsed_ms:-1}),/schema_mismatch/);
  assert.throws(()=>event(stream,'failed',{reason:'secret-canary',phase:'ready'}),/schema_mismatch/);
  assert.throws(()=>stream.line('{'),/malformed_json/);
  assert.equal(stream.finish({status:0,milliseconds:1}).status,'host_failed');
});

test('host failures preserve phase and partial no-delivery evidence', () => {
  const stream = hostStream('missing_model','fixed_phrase');
  prefix(stream);
  event(stream,'failure_detected',{no_request_on_failure:true});
  event(stream,'failed',{reason:'draft_not_observed',phase:'failure_detected'});
  const result = stream.finish({status:1,milliseconds:10});
  assert.equal(result.reason,'draft_not_observed');
  assert.equal(result.last_stage,'failure_detected');
  assert.equal(result.no_request_on_failure,true);
  assert.equal(result.evidence,null);
});

test('success evidence requires the configured client version', () => {
  for (const host_version of [completion.host_version, '0.0.0 (Claude Code)']) {
    const stream = hostStream('success','fixed_phrase');
    const emit = (name, fields = {}) => stream.line(JSON.stringify({protocol:1,event:name,
      scenario:'success',strategy:'fixed_phrase',elapsed_ms:1,...fields}));
    for (const name of requiredStages('success').slice(0,-1)) emit(name);
    if (host_version === completion.host_version) {
      emit('completed', {...completion,host_version});
      assert.equal(stream.finish({status:0,milliseconds:10}).status,'passed');
    } else {
      assert.throws(() => emit('completed', {...completion,host_version}), /schema_mismatch/);
    }
  }
});

function completeCase(scenario, strategy) {
  const stream = hostStream(scenario,strategy), contract = hostCases[scenario];
  for (const name of requiredStages(scenario)) stream.line(JSON.stringify({protocol:1,event:name,scenario,strategy,elapsed_ms:45000,
    ...(name === 'failure_detected' ? {no_request_on_failure:true} : {}),
    ...(name === 'deadline_verified' ? {deadline_ms:45000,worker_stopped:true} : {}),
    ...(name === 'completed' ? {...completion,injection:contract.injection,recovery:contract.recovery} : {})}));
  return {schema:2,platform:'darwin',arch:'arm64',scenario,strategy,suite:contract.suite,adapter_id:'composer_2_1_322',
    candidate_sha256:'c'.repeat(64),recorded_at:'2026-11-11T00:00:00Z',scope:'configured_loopback_receiver',
    identity:{harness:{test:'a'.repeat(64)},models:{model:'b'.repeat(64)},instrumentation_sha256:contract.suite === 'instrumented_failure'?'d'.repeat(64):null},
    ...stream.finish({status:0,milliseconds:45000})};
}

test('release matrix rejects absent, duplicated, mixed, or source-only evidence', () => {
  const matrix = Object.keys(hostCases).flatMap(scenario=>['fixed_phrase','omit'].map(strategy=>completeCase(scenario,strategy)));
  const candidate = 'c'.repeat(64);
  assert.equal(validateMatrix(matrix,candidate).cases,18);
  assert.throws(()=>validateMatrix(matrix.slice(1),candidate),/incomplete_matrix/);
  assert.throws(()=>validateMatrix([...matrix.slice(1),matrix[1]],candidate),/duplicate/);
  for (const mutate of [row=>row.suite='source_checkout', row=>row.candidate_sha256='e'.repeat(64),
    row=>row.status='host_failed', row=>row.evidence.host_version='0.0.0 (Claude Code)',
    row=>row.identity.models.model='f'.repeat(64), row=>row.evidence.host_sha256='f'.repeat(64),
    row=>row.events.splice(2,1), row=>row.events.reverse()]) {
    const changed=structuredClone(matrix); mutate(changed[0]);
    assert.throws(()=>validateMatrix(changed,candidate));
  }
});

test('deadline evidence requires elapsed production timeout and worker cleanup', () => {
  for (const fields of [{deadline_ms:20,worker_stopped:true},{deadline_ms:45000,worker_stopped:false}]) {
    const stream=hostStream('worker_deadline','omit');
    for (const name of requiredStages('worker_deadline')) {
      const line=JSON.stringify({protocol:1,event:name,scenario:'worker_deadline',strategy:'omit',elapsed_ms:45000,no_request_on_failure:true,...fields});
      if (name === 'deadline_verified') { assert.throws(()=>stream.line(line),/schema_mismatch/); break; }
      stream.line(line);
    }
  }
});

test('repeated or out-of-order readiness cannot qualify a run', () => {
  const stream=hostStream('missing_model','fixed_phrase');
  event(stream,'ready');
  assert.throws(()=>event(stream,'ready'),/schema_mismatch/);
  assert.throws(()=>event(stream,'filter_ready'),/schema_mismatch/);
});

test('child timeout gives a terminal harness time to clean up', async () => {
  const result=await child(process.execPath,['-e',
    'process.on("SIGTERM",()=>{console.log("cleanup");process.exit(0)});setInterval(()=>{},1000)'],
    {timeout:500,killGraceMs:1000});
  assert.equal(result.error,'child_timeout');
  assert.match(result.stdout,/cleanup/);
});

test('real child exit, timeout, and malformed output retain safe checkpoints', async () => {
  for (const [tail,reason] of [['process.exit(7)','process_exit'],['setInterval(()=>{},1000)','child_timeout'],['console.log("{")','malformed_json']]) {
    const checkpoints=[],stream=hostStream('missing_model','fixed_phrase',e=>checkpoints.push(e));
    const code='console.log(JSON.stringify({protocol:1,event:"ready",scenario:"missing_model",strategy:"fixed_phrase",elapsed_ms:0,private:"secret-canary"}));'+tail;
    const result=await child(process.execPath,['-e',code],{timeout:500,onLine:stream.line});
    const row=stream.finish(result);
    assert.equal(row.reason,reason);
    assert.equal(checkpoints.length,1);
    assert.ok(!JSON.stringify({row,checkpoints}).includes('secret-canary'));
  }
});

test('environment metadata is numeric machine context, not a quiet-host assertion', () => {
  const sample=environmentSample();
  assert.equal(sample.load_average.length,3);
  assert.ok(sample.load_average.every(n=>Number.isFinite(n)&&n>=0));
  assert.ok(sample.logical_cpus>0);
  assert.ok(Number.isFinite(Date.parse(sample.recorded_at)));
});
