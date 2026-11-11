import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {window_releases, validate_inventory, job_matrix, targets} from '../scripts/release/compatibility-inventory.mjs';
import {aggregate, markdown} from '../scripts/release/compatibility-report.mjs';
import {hostStream, requiredStages, hostCases} from '../scripts/bench/host.mjs';

const as_of = '2026-11-11T12:00:00.000Z', start = '2026-08-13T12:00:00.000Z';
const release = (version, published_at = as_of, fields = {}) => ({tag_name:'v'+version, published_at, id:Number(version.split('.').at(-1)), ...fields});
const artifact = (version,target) => ({name:`@anthropic-ai/claude-code-${target}`,version,
  url:`https://registry.npmjs.org/@anthropic-ai/claude-code-${target}/-/claude-code-${target}-${version}.tgz`,
  integrity:'sha512-'+Buffer.alloc(64).toString('base64')});
const inventory = () => ({schema:1,source:'https://api.github.com/repos/anthropics/claude-code/releases',as_of,
  window_start:start,window_days:90,releases:window_releases([release('2.1.322'),release('2.1.321')],as_of)
    .map(row=>({...row,artifacts:Object.fromEntries(Object.keys(targets).map(target=>[target,artifact(row.version,target)]))}))});
const digest = 'a'.repeat(64), candidate = 'b'.repeat(64), inputs = {harness:'c'.repeat(64)};

test('90-day window uses inclusive UTC publication bounds and excludes unstable releases', () => {
  const rows = [release('2.1.1',start),release('2.1.2',as_of),release('2.1.3','2026-08-13T11:59:59Z'),
    release('2.1.4','2026-11-11T12:00:01Z'),release('2.1.5',as_of,{draft:true}),
    release('2.1.6',as_of,{prerelease:true}),release('2.1.7-beta.1')];
  assert.deepEqual(window_releases(rows,as_of).map(row=>row.version),['2.1.1','2.1.2']);
  assert.throws(()=>window_releases([rows[0],rows[0]],as_of),/identity/);
  assert.throws(()=>window_releases(rows,'bad'),/date/);
});

test('inventory rejects mutable artifacts, duplicate clients, invalid bounds and targets', () => {
  const valid = inventory();
  assert.equal(job_matrix(valid).include.length,3);
  assert.ok(job_matrix(valid).include.every(job=>job.versions==='2.1.321,2.1.322'));
  for (const mutate of [value=>value.window_days=91, value=>value.window_start=as_of,
    value=>value.releases.push(value.releases[0]), value=>delete value.releases[0].artifacts['linux-x64'],
    value=>value.releases[0].artifacts['linux-x64'].integrity='sha1-bad',
    value=>value.releases[0].artifacts['linux-x64'].url='https://evil.test/client.tgz']) {
    const value = structuredClone(valid); mutate(value); assert.throws(()=>validate_inventory(value));
  }
  valid.releases[0].artifacts['linux-x64'] = null;
  assert.doesNotThrow(()=>validate_inventory(valid));
});

function cases(version, target) {
  const [platform,arch] = target.split('-');
  return Object.entries(hostCases).flatMap(([scenario,contract])=>['fixed_phrase','omit'].map(strategy=>{
    const stream = hostStream(scenario,strategy,undefined,version+' (Claude Code)');
    for (const event of requiredStages(scenario)) stream.line(JSON.stringify({protocol:1,event,scenario,strategy,elapsed_ms:45000,
      no_request_on_failure:true,deadline_ms:45000,worker_stopped:true,host_version:version+' (Claude Code)',
      host_sha256:digest,submission_to_provider_ms:30,samples:1,injection:contract.injection,recovery:contract.recovery}));
    return {schema:2,platform,arch,scenario,strategy,suite:contract.suite,adapter_id:'composer_2_1_322',
      candidate_sha256:candidate,recorded_at:as_of,scope:'configured_loopback_receiver',
      identity:{harness:inputs,models:{model:digest},instrumentation_sha256:contract.suite==='instrumented_failure'?digest:null},
      ...stream.finish({status:0,milliseconds:45000})};
  }));
}

function assessments(value = inventory()) {
  return value.releases.flatMap(release=>Object.keys(targets).map(target=>{
    const mapped = release.version === '2.1.322';
    return {schema:1,version:release.version,target,inventory_sha256:digest,candidate_sha256:candidate,
      artifact:release.artifacts[target],recorded_at:as_of,qualification_inputs:inputs,host_sha256:digest,
      adapter_id:mapped?'composer_2_1_322':null,status:mapped?'passed':'unsupported_api',reason:mapped?null:'no_reviewed_adapter',
      checks:Object.fromEntries(['manifest','handlers','types_fetch','types','installed'].map(name=>[name,{status:'passed'}])),
      cases:mapped?cases(release.version,target):[]};
  }));
}

test('complete native evidence qualifies reviewed mappings while exposing unsupported gaps', () => {
  const report = aggregate(inventory(),digest,assessments(),candidate,inputs);
  assert.equal(report.status,'passed'); assert.equal(report.coverage,'partial');
  assert.equal(report.supported.length,3); assert.equal(report.assessments.length,6);
  assert.ok(report.supported.every(row=>row.client_version==='2.1.322'));
  assert.match(markdown(report),/no_reviewed_adapter/);
  assert.match(markdown(report),/whole-host/);
});

test('multiple exact clients can qualify the same reviewed adapter without equal platform binaries', () => {
  const rows = assessments();
  for (const row of rows) {
    row.adapter_id = 'composer_2_1_322'; row.status = 'passed'; row.reason = null;
    row.cases = cases(row.version,row.target);
    const binary = row.target === 'linux-x64' ? 'f'.repeat(64) : digest;
    row.host_sha256 = binary;
    for (const entry of row.cases) {
      entry.evidence.host_sha256 = binary;
      entry.events.at(-1).host_sha256 = binary;
    }
  }
  const adapters = [{id:'composer_2_1_322',client_versions:['2.1.321','2.1.322']}];
  const report = aggregate(inventory(),digest,rows,candidate,inputs,adapters);
  assert.equal(report.status,'passed'); assert.equal(report.coverage,'complete');
  assert.equal(report.supported.length,6);
});

test('missing, stale, partial and mixed evidence cannot qualify an advertised client', () => {
  const original = assessments();
  for (const mutate of [rows=>rows.pop(), rows=>rows[3].cases.pop(), rows=>rows[3].candidate_sha256=digest,
    rows=>rows[3].qualification_inputs={}, rows=>rows[3].checks.handlers.status='failed',
    rows=>rows[3].status='unsupported_api', rows=>rows[3].cases[0].suite='source_checkout',
    rows=>rows[3].cases.forEach(row=>row.identity.models.model=candidate),
    rows=>rows[3].cases[0].events.reverse(),rows=>rows[3].host_sha256=candidate]) {
    const rows = structuredClone(original); mutate(rows);
    assert.equal(aggregate(inventory(),digest,rows,candidate,inputs).status,'failed');
  }
  assert.throws(()=>aggregate(inventory(),digest,[...original,original[0]],candidate,inputs),/duplicate/);
  assert.throws(()=>aggregate(inventory(),digest,original,candidate,inputs,[{client_versions:['0.0.0']}]),/outside_window/);
});

test('unsupported and unavailable are assessed gaps, never inferred from missing rows', () => {
  const rows = assessments();
  rows[0].checks = {};
  assert.equal(aggregate(inventory(),digest,rows,candidate,inputs).status,'failed');
  rows[0].status='unavailable_artifact'; rows[0].reason='native_download_unavailable';
  assert.equal(aggregate(inventory(),digest,rows,candidate,inputs).status,'passed');
});

test('checked-in initial inventory is frozen and covers all three native platforms', async () => {
  const value = validate_inventory(JSON.parse(await readFile(new URL('../tools/host/releases-2026-11-11.json',import.meta.url))));
  assert.equal(value.releases.length,75);
  assert.equal(job_matrix(value).include.length,57);
  assert.ok(job_matrix(value).include.every(job=>job.versions.split(',').length<=4));
});
