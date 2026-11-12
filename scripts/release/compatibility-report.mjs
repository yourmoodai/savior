import {readFile, readdir, writeFile, mkdir} from 'node:fs/promises';
import {resolve, dirname} from 'node:path';
import {pathToFileURL} from 'node:url';
import {isDeepStrictEqual} from 'node:util';
import {catalog} from '../../hooks/compatibility-catalog.ts';
import {select_adapter} from '../../hooks/compatibility.ts';
import {validateMatrix} from './host-matrix.mjs';
import {validate_inventory, targets, sha256} from './compatibility-inventory.mjs';

const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
async function files_in(directory) {
  const files = [];
  for (const entry of await readdir(directory, {withFileTypes:true})) {
    if (entry.name === '__pycache__') continue;
    const file = `${directory}/${entry.name}`;
    if (entry.isDirectory()) files.push(...await files_in(file));
    else if (entry.isFile()) files.push(file);
  }
  return files.sort();
}

// Input changes invalidate prior evidence even when the candidate ZIP is unchanged.
export async function qualification_inputs() {
  const files = ['package.json','package-lock.json','.gitattributes','scripts/fetch-types.mjs',
    '.github/workflows/compatibility.yml','.github/actions/model-cache/action.yml',
    ...catalog.adapters.map(adapter=>adapter.typecheck),
    'scripts/test-installed-mod.py','tests/plugin.test.ts','tests/fixtures/host-failure.mjs',
    ...await files_in('hooks'), ...await files_in('src'),
    ...await files_in('scripts/release'), ...await files_in('scripts/bench'),
    ...await files_in('models')];
  return Object.fromEntries(await Promise.all(files.sort().map(async file=>[file, sha256(await readFile(file))])));
}

export function aggregate(inventory, inventory_sha256, rows, candidate_sha256, inputs, adapters = catalog.adapters) {
  validate_inventory(inventory);
  if (!hash(inventory_sha256) || !hash(candidate_sha256)) throw Error('qualification_identity_missing');
  const versions = new Set(inventory.releases.map(release=>release.version));
  const advertised = adapters.flatMap(adapter=>adapter.client_versions);
  if (!advertised.length || new Set(advertised).size !== advertised.length || advertised.some(version=>!versions.has(version))) {
    throw Error('catalog_ambiguous_or_outside_window');
  }
  const seen = new Map(), errors = [], supported = [], table = [];
  let common;
  for (const row of rows) {
    const key = `${row.version}/${row.target}`;
    if (seen.has(key)) throw Error('duplicate_assessment');
    if (!versions.has(row.version) || !Object.hasOwn(targets,row.target)) throw Error('unknown_assessment');
    seen.set(key,row);
  }
  for (const release of inventory.releases) for (const target of Object.keys(targets)) {
    const key = `${release.version}/${target}`, row = seen.get(key), adapter = select_adapter(release.version,adapters);
    let reason = null;
    if (!row) reason = 'missing_assessment';
    else if (row.schema !== 1 || row.inventory_sha256 !== inventory_sha256 || row.candidate_sha256 !== candidate_sha256 ||
             row.adapter_id !== (adapter?.id ?? null) || !isDeepStrictEqual(row.artifact,release.artifacts[target]) ||
             !isDeepStrictEqual(row.qualification_inputs,inputs) || !Number.isFinite(Date.parse(row.recorded_at)) ||
             !['passed','failed','unsupported_api','unavailable_artifact'].includes(row.status)) reason = 'invalid_assessment_identity';
    else if (row.status === 'passed') {
      try {
        if (!adapter || row.reason !== null || !hash(row.host_sha256) ||
            !['manifest','handlers','types_fetch','types','installed'].every(name=>row.checks?.[name]?.status === 'passed')) throw Error('incomplete_checks');
        const matrix = validateMatrix(row.cases,candidate_sha256,adapters);
        if (`${matrix.platform}-${matrix.arch}` !== target || matrix.host_version !== release.version+' (Claude Code)' ||
            matrix.host_sha256 !== row.host_sha256) throw Error('matrix_identity_mismatch');
        const shared = {harness:matrix.harness, models:matrix.models, instrumentation:matrix.instrumentation_sha256};
        if (common && !isDeepStrictEqual(shared,common)) throw Error('mixed_qualification_inputs');
        common = shared;
        supported.push({client_version:release.version, target, adapter_id:adapter.id,
          candidate_sha256, host_sha256:row.host_sha256, verified_at:row.recorded_at});
      } catch { reason = 'invalid_native_evidence'; }
    } else if (adapter) reason = 'advertised_client_not_passed';
    else if (row.status === 'unsupported_api' && (row.reason !== 'no_reviewed_adapter' || !hash(row.host_sha256) ||
      !['manifest','handlers'].every(name=>['passed','failed'].includes(row.checks?.[name]?.status)))) reason = 'unsupported_without_probe';
    else if (row.status === 'unavailable_artifact' && !['native_package_absent','native_download_unavailable'].includes(row.reason)) reason = 'invalid_artifact_gap';
    else if (row.status === 'failed') reason = 'assessment_failed';
    if (reason) errors.push({version:release.version,target,reason});
    table.push({version:release.version, published_at:release.published_at, target,
      status:reason ? 'failed' : row.status, reason:reason ?? row.reason, verified_at:row?.recorded_at ?? null});
  }
  return {schema:1,status:errors.length ? 'failed' : 'passed', coverage:supported.length === table.length ? 'complete' : 'partial',
    scope:'terminal_composer_configured_loopback_receiver', inventory_sha256, candidate_sha256,
    as_of:inventory.as_of, window_start:inventory.window_start, window_days:90,
    qualification_inputs:inputs, supported, assessments:table, errors};
}

export function markdown(report) {
  const lines = ['# Claude Code compatibility', '',
    `Window: ${report.window_start} through ${report.as_of} (90 days, UTC).`, '',
    `Candidate: \`${report.candidate_sha256}\`. Gate: **${report.status}**. Window coverage: **${report.coverage}**.`, '',
    'Only passed rows qualify this candidate. Unsupported rows have no reviewed adapter; they do not establish an upstream API defect.', '',
    '| Client | Published (UTC) | Platform | Result | Reason | Verified (UTC) |',
    '| --- | --- | --- | --- | --- | --- |'];
  for (const row of report.assessments) lines.push(`| ${row.version} | ${row.published_at} | ${row.target} | ${row.status} | ${row.reason ?? '—'} | ${row.verified_at ?? '—'} |`);
  lines.push('', 'Evidence covers terminal composer submissions to the configured loopback receiver. Desktop, cloud clients, and whole-host traffic are outside this qualification.', '');
  return lines.join('\n');
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const [inventory_file, directory, candidate, output] = process.argv.slice(2);
  if (inventory_file === 'inputs') console.log(JSON.stringify(await qualification_inputs()));
  else if (inventory_file && directory && candidate && output) {
    const bytes = await readFile(inventory_file);
    const files = (await files_in(directory)).filter(file=>file.endsWith('/assessment.json'));
    const rows = await Promise.all(files.map(async file=>JSON.parse(await readFile(file))));
    const report = aggregate(JSON.parse(bytes),sha256(bytes),rows,candidate,await qualification_inputs());
    await mkdir(dirname(resolve(output)),{recursive:true});
    await writeFile(output,JSON.stringify(report,null,2)+'\n',{flag:'wx'});
    await writeFile(output+'.md',markdown(report),{flag:'wx'});
    console.log(JSON.stringify({status:report.status,supported:report.supported.length,assessments:report.assessments.length,errors:report.errors.length}));
    if (report.status !== 'passed') process.exitCode = 1;
  } else throw Error('usage: compatibility-report.mjs inventory directory candidate_sha256 output | inputs');
}
