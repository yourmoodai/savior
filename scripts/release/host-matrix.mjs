import {readFile, readdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {isDeepStrictEqual} from 'node:util';
import {hostCases, hostStream} from '../bench/host.mjs';
import {catalog} from '../../hooks/compatibility-catalog.ts';
import {select_adapter} from '../../hooks/compatibility.ts';

const sha256 = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const hashMap = value => value && Object.keys(value).length > 0 && Object.values(value).every(sha256);
export const reportNames = Object.keys(hostCases).flatMap(scenario =>
  ['fixed_phrase','omit'].map(strategy => `${strategy}-${scenario}.json`));

// Publication requires one complete matrix from the same candidate and native host.
export function validateMatrix(reports, candidate, adapters = catalog.adapters) {
  if (!sha256(candidate) || reports.length !== reportNames.length) throw Error('incomplete_matrix');
  const seen = new Set();
  let baseline;
  for (const report of reports) {
    const {scenario, strategy, identity, evidence} = report;
    const name = `${strategy}-${scenario}.json`;
    const contract = hostCases[scenario];
    if (!reportNames.includes(name) || seen.has(name)) throw Error('duplicate_or_unknown_case');
    seen.add(name);
    if (report.schema !== 2 || report.status !== 'passed' || report.reason !== null ||
        report.suite !== contract.suite || report.candidate_sha256 !== candidate ||
        report.scope !== 'configured_loopback_receiver' || report.last_stage !== 'completed' ||
        !Number.isFinite(Date.parse(report.recorded_at)) ||
        !['darwin-arm64','linux-x64','win32-x64'].includes(`${report.platform}-${report.arch}`) ||
        !hashMap(identity?.harness) || !hashMap(identity?.models) ||
        !sha256(evidence?.host_sha256) || !report.adapter_id ||
        report.adapter_id !== select_adapter(evidence?.host_version?.split(' ')[0], adapters)?.id ||
        (contract.suite === 'instrumented_failure' ? !sha256(identity.instrumentation_sha256) : identity.instrumentation_sha256 !== null)) {
      throw Error('invalid_case_evidence');
    }
    const stream = hostStream(scenario, strategy, undefined, evidence.host_version);
    for (const event of report.events) stream.line(JSON.stringify(event));
    const verified = stream.finish({status:0,milliseconds:report.process_ms});
    if (verified.status !== 'passed' || !isDeepStrictEqual(verified.evidence, evidence)) throw Error('invalid_case_events');
    const comparable = {platform:report.platform, arch:report.arch,
      host_version:evidence.host_version, host_sha256:evidence.host_sha256,
      harness:identity.harness, models:identity.models};
    if (baseline && !isDeepStrictEqual(comparable, baseline)) throw Error('mixed_matrix_identity');
    baseline = comparable;
  }
  const faults = reports.filter(report => report.suite === 'instrumented_failure');
  if (new Set(faults.map(report => report.identity.instrumentation_sha256)).size !== 1) throw Error('mixed_instrumentation');
  return {schema:2,status:'passed',scope:'configured_loopback_receiver',candidate_sha256:candidate,
    cases:reports.length,...baseline,instrumentation_sha256:faults[0].identity.instrumentation_sha256};
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const [directory, candidate] = process.argv.slice(2);
  let summary;
  try {
    const names = (await readdir(directory)).filter(name => name.endsWith('.json')).sort();
    if (!isDeepStrictEqual(names, [...reportNames].sort())) throw Error('incomplete_matrix');
    summary = validateMatrix(await Promise.all(names.map(async name => JSON.parse(await readFile(resolve(directory,name))))), candidate);
  } catch (error) {
    const reasons = ['incomplete_matrix','duplicate_or_unknown_case','invalid_case_evidence','invalid_case_events','mixed_matrix_identity','mixed_instrumentation','schema_mismatch'];
    summary = {schema:2,status:'failed',reason:reasons.includes(error.message) ? error.message : 'unreadable_matrix'};
    process.exitCode = 1;
  }
  await writeFile(resolve(directory,'matrix.json'), JSON.stringify(summary,null,2)+'\n', {flag:'wx',mode:0o600});
  console.log(JSON.stringify({status:summary.status,cases:summary.cases,reason:summary.reason}));
}
