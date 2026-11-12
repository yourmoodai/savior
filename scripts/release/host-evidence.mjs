import {mkdir, writeFile, readFile, readdir} from 'node:fs/promises';
import {appendFileSync, createReadStream} from 'node:fs';
import {resolve} from 'node:path';
import {createHash} from 'node:crypto';
import {child} from '../bench/common.mjs';
import {hostStream, hostCases} from '../bench/host.mjs';
import {select_adapter} from '../../hooks/compatibility.ts';

const scenario = process.env.SAVIOR_HOST_SCENARIO;
const strategy = process.env.SAVIOR_REPLACEMENT_STRATEGY;
const source = process.env.SAVIOR_HOST_SUITE === 'source_checkout';
const candidate = source ? null : process.env.SAVIOR_CANDIDATE_SHA256;
const directory = process.env.SAVIOR_HOST_REPORT_DIR;
if (!hostCases[scenario] || !['fixed_phrase','omit'].includes(strategy) || process.argv.length !== 3 ||
    !directory || !source && !/^[a-f0-9]{64}$/.test(candidate ?? '')) throw Error('invalid_host_gate');
const digest = value => createHash('sha256').update(value).digest('hex');
const fileDigest = async file => {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  return hash.digest('hex');
};
const hashes = async files => Object.fromEntries(await Promise.all(files.map(async file => [file, digest(await readFile(file))])));
const harness_files = ['scripts/test-installed-mod.py','scripts/release/host_fixture.py','scripts/release/host-cases.json',
  'scripts/release/host-evidence.mjs','scripts/release/host-matrix.mjs','scripts/bench/host.mjs','scripts/bench/common.mjs',
  'tools/host/package.json','tools/host/package-lock.json','hooks/compatibility-catalog.ts','hooks/compatibility.ts',
  'hooks/register.tsx','hooks/runtime.ts','hooks/distribution.ts','hooks/review.ts','src/protocol.ts','bridge/cli.mjs'];
const model = resolve(process.env.SAVIOR_MODEL_DIR);
const model_files = (await readdir(model)).filter(file => /\.(json|txt|bin|safetensors)$/.test(file)).sort();
const modelHashes = async () => Object.fromEntries(await Promise.all(model_files.map(async file => [file, await fileDigest(resolve(model,file))])));
const model_hashes = await modelHashes();
if (!model_files.length) throw Error('model_identity_missing');
const identity = {harness:await hashes(harness_files), models:model_hashes,
  instrumentation_sha256:hostCases[scenario].suite === 'instrumented_failure' ? digest(await readFile('tests/fixtures/host-failure.mjs')) : null};
await mkdir(directory, {recursive:true});
const stem = resolve(directory, `${strategy}-${scenario}`);
await writeFile(stem+'.events.jsonl', '', {flag:'wx',mode:0o600});
const expected_version = process.env.SAVIOR_HOST_VERSION;
const stream = hostStream(scenario, strategy, event => appendFileSync(stem+'.events.jsonl',JSON.stringify(event)+'\n'),
  expected_version ? expected_version+' (Claude Code)' : undefined);
const execution = await child(process.argv[2], ['scripts/test-installed-mod.py'], {
  timeout:240000, onLine:stream.line, killGraceMs:10000,
});
const report = {schema:2, platform:process.platform, arch:process.arch, scenario, strategy,
  suite:source ? 'source_checkout' : hostCases[scenario].suite,
  candidate_sha256:candidate, identity, recorded_at:new Date().toISOString(),
  scope:'configured_loopback_receiver', ...stream.finish(execution)};
report.adapter_id = select_adapter(report.evidence?.host_version?.split(' ')[0])?.id ?? null;
if (JSON.stringify(identity.harness) !== JSON.stringify(await hashes(harness_files))) {
  report.status='host_failed'; report.reason='harness_changed';
}
if (JSON.stringify(identity.models) !== JSON.stringify(await modelHashes()) ||
    identity.instrumentation_sha256 && identity.instrumentation_sha256 !== await fileDigest('tests/fixtures/host-failure.mjs')) {
  report.status='host_failed'; report.reason='inputs_changed';
}
await writeFile(stem+'.json', JSON.stringify(report,null,2)+'\n', {flag:'wx',mode:0o600});
console.log(JSON.stringify({scenario,strategy,status:report.status,reason:report.reason}));
if (report.status !== 'passed') process.exitCode = 1;
