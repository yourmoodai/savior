import {readFileSync} from 'node:fs';

const host_version = JSON.parse(readFileSync(new URL('../../tools/host/package.json', import.meta.url))).dependencies['@anthropic-ai/claude-code'] + ' (Claude Code)';
export const hostCases = JSON.parse(readFileSync(new URL('../release/host-cases.json', import.meta.url)));
export function requiredStages(scenario) {
  if (!hostCases[scenario]) throw Error('invalid_scenario');
  const start = ['ready','pane', ...(scenario === 'startup_failure' ? [] : ['filter_ready'])];
  return [...start, ...(hostCases[scenario].recovery === null ? ['submission','provider_received'] :
    ['failure_submission','failure_detected', ...(scenario === 'worker_deadline' ? ['deadline_verified'] : []), 'draft_ready',
     ...(scenario === 'startup_failure' ? [] : ['recovery_submission','provider_received'])]), 'completed'];
}
const phases = ['filter_ready','deadline_verified','ready','trust_selection','trusted','pane','metrics','consent','submission',
  'metrics_recorded','failure_submission','failure_detected','draft_ready','recovery_submission',
  'provider_received','completed','failed'];
const reasons = ['installed_host_timeout','installed_host_exited','unexpected_authentication',
  'provider_request_observed','original_reached_provider','draft_not_observed','unexpected_consent',
  'ui_worker_mismatch','host_execution_failed','host_wrapper_requires_native_binary',
  'host_prerequisite_missing','host_version_unsupported','deadline_not_verified'];
const finite = value => Number.isFinite(value) && value >= 0;

// Host output is untrusted: only schema-checked, content-free evidence is persisted.
export function hostStream(scenario, strategy, checkpoint = () => {}, expected_version = host_version) {
  if (!/^\d+\.\d+\.\d+ \(Claude Code\)$/.test(expected_version)) throw Error('invalid_host_version');
  const events = [], required = requiredStages(scenario), seen = new Set();
  let required_index = 0;
  let evidence = null, reason = null, terminal = false;
  const line = text => {
    if (!text.startsWith('{')) return;
    let value;
    try { value = JSON.parse(text); } catch { throw Error('malformed_json'); }
    if (reasons.includes(value.error) && Object.keys(value).length === 1) {
      reason = value.error;
      return;
    }
    if (terminal || seen.has(value.event) || value.protocol !== 1 || value.scenario !== scenario || value.strategy !== strategy ||
      !phases.includes(value.event) || !finite(value.elapsed_ms) || value.elapsed_ms < (events.at(-1)?.elapsed_ms ?? 0)) throw Error('schema_mismatch');
    if (required.includes(value.event)) {
      if (required[required_index] !== value.event) throw Error('schema_mismatch');
    }
    const event = {protocol:1,event:value.event,scenario,strategy,elapsed_ms:value.elapsed_ms};
    if (value.event === 'deadline_verified') {
      if (scenario !== 'worker_deadline' || !finite(value.deadline_ms) || value.deadline_ms < 44000 || value.worker_stopped !== true) throw Error('schema_mismatch');
      Object.assign(event, {deadline_ms:value.deadline_ms, worker_stopped:true});
    }
    if (value.event === 'failure_detected') {
      if (value.no_request_on_failure !== true) throw Error('schema_mismatch');
      event.no_request_on_failure = true;
    }
    if (value.event === 'failed') {
      if (!reasons.includes(value.reason) || !phases.includes(value.phase)) throw Error('schema_mismatch');
      reason = value.reason;
      event.reason = reason;
      event.phase = value.phase;
      for (const key of ['draft_text_visible','prompt_marker_visible','draft_words_visible']) {
        if (typeof value[key] === 'boolean') event[key] = value[key];
      }
      terminal = true;
    }
    if (value.event === 'completed') {
      if (value.host_version !== expected_version || !/^[a-f0-9]{64}$/.test(value.host_sha256) ||
        !finite(value.submission_to_provider_ms) || value.samples !== 1 || required_index !== required.length - 1) throw Error('schema_mismatch');
      const completed = {scenario,strategy,host_version:value.host_version,host_sha256:value.host_sha256,
        submission_to_provider_ms:value.submission_to_provider_ms,samples:1};
      const contract = hostCases[scenario];
      if (contract.recovery !== null) {
        if (value.no_request_on_failure !== true || value.injection !== contract.injection || value.recovery !== contract.recovery) throw Error('schema_mismatch');
        Object.assign(completed, {no_request_on_failure:true, injection:contract.injection, recovery:contract.recovery});
      }
      evidence = completed;
      Object.assign(event,evidence);
      terminal = true;
    }
    if (required.includes(value.event)) required_index++;
    seen.add(value.event);
    events.push(event);
    checkpoint(event);
  };
  return {line, finish(result) {
    const passed = result.status === 0 && !result.error && evidence !== null && reason === null;
    return {status:passed?'passed':result.error??'host_failed',evidence,
      reason:reason??(passed?null:result.error??(result.status!==0?'process_exit':'incomplete_host_report')),
      last_stage:events.at(-1)?.phase??events.at(-1)?.event??null,
      no_request_on_failure:events.some(e => e.event === 'failure_detected') ? true : null,
      protocol:1,events,process_ms:result.milliseconds};
  }};
}
