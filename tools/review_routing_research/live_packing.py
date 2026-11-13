"""One explicitly authorized paired packing trial with its own durable $4 cap."""
import argparse
from contextlib import contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

from packing import anchors, candidate, count_tokens, digest, messages, plan, render
from qualify_packing import freeze as freeze_protocol
import adversarial_review as reviewer
import bedrock_client
from diagnostics import capture
from egm import FOCUS
from model_catalog import manifest as routing_manifest, select, selected, current_pricing
from models import account, aws
from prompt_profiles import select as select_prompts
from research_reporting import deterministic

EXPERIMENT = 'packing-quality-v1'
ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('packing_budget', ROOT.parent/'adversarial_reviewer/research_ledger.py')
budget = importlib.util.module_from_spec(spec)
spec.loader.exec_module(budget)
# An isolated instance of the tested ledger implementation keeps the completed
# experiment's constants, journal, and checkpoint untouched.
budget.EXPERIMENT = EXPERIMENT
Ledger, Stopped = budget.ResearchLedger, budget.ResearchStopped


def state_directory():
    common = subprocess.run(['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                            capture_output=True, text=True, check=True).stdout.strip()
    return Path(common)/'reviewer-research'/EXPERIMENT


def sources():
    files = [*ROOT.glob('*.py'), *ROOT.parent.joinpath('adversarial_reviewer').glob('*.py')]
    return {str(p.relative_to(ROOT.parent)): digest(p.read_text()) for p in sorted(files)}


def environment():
    if os.environ.get('AWS_PROFILE') != 'awill' or os.environ.get('AWS_REGION') != 'us-east-1':
        raise Stopped('authorized awill profile and us-east-1 region required')
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        raise Stopped('research exemption cannot run in ci')
    if any(os.environ.get(k) for k in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN')):
        raise Stopped('ambient credentials conflict with sso identity')
    if any(k.startswith('AWS_ENDPOINT_URL') and v for k, v in os.environ.items()):
        raise Stopped('endpoint override conflicts with frozen routing')


def verify(frozen):
    environment()
    if frozen['execution_sources'] != sources() or frozen['routing'] != routing_manifest():
        raise Stopped('execution configuration changed; preserve ledger and stop')
    if frozen['account'] != account() or frozen['cap_nanodollars'] != budget.CAP:
        raise Stopped('account or allowance mismatch')
    if set(frozen['routing']['routing']['roles'].values()) != {'deepseek_v3_2'}:
        raise Stopped('deepseek routing required; no substitution')
    for role in FOCUS:
        current_pricing(selected(role))


def prepare(protocol):
    environment()
    current = freeze_protocol()
    # The previously reviewed prompt protocol stays immutable. New execution code
    # receives a separate fingerprint before the first reservation.
    for key in current:
        if key not in ('fingerprint', 'source_fingerprint') and current[key] != protocol.get(key):
            raise Stopped('prepared prompts or fixtures changed')
    if protocol['fingerprint'] != digest({k: v for k, v in protocol.items() if k != 'fingerprint'}):
        raise Stopped('invalid protocol fingerprint')
    caller = aws('sts', 'get-caller-identity')
    if caller['Account'] != account():
        raise Stopped('aws account differs from configured trust boundary')
    frozen = {'experiment': EXPERIMENT, 'cap_nanodollars': budget.CAP,
              'authorization': 'explicit new $4 research cap; completed review-designs-v1 grant remains closed',
              'account': caller['Account'], 'profile': 'awill', 'region': 'us-east-1',
              'routing': routing_manifest(), 'execution_sources': sources(), 'protocol': protocol}
    verify(frozen)
    return Ledger.initialize(state_directory(), frozen)


@contextmanager
def transport(ledger, frozen, check=verify):
    old_ledger, old_invoke = bedrock_client.Ledger, bedrock_client.invoke
    overrides = {'BEDROCK_REVIEW_ENABLED': 'true', 'REVIEW_COST_POLICY': 'false',
                 'REVIEW_BUDGET_GRANT': EXPERIMENT, 'REVIEW_BUDGET_LEDGER': str(ledger.path),
                 'REVIEW_BUDGET_PHASE': 'review', 'REVIEW_CACHE_DISABLED': 'true', 'REVIEW_RESULT_CACHE': None}
    previous = {k: os.environ.get(k) for k in overrides}

    def bound(path):
        if Path(path) != ledger.path:
            raise Stopped('research ledger path changed')
        return ledger

    def invoke(operation, payload, deadline):
        call = ledger.calls(ledger.events())[-1]
        ledger.append({'kind': 'transport_started', 'call_id': call['id'], 'request': payload})
        try:
            result = old_invoke(operation, payload, deadline)
        except BaseException as error:
            ledger.append({'kind': 'transport_failed', 'call_id': call['id'], 'error_type': type(error).__name__})
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise Stopped('transport failed; reservation retained; no routing fallback') from error
        ledger.append({'kind': 'transport_response', 'call_id': call['id'], 'response': result})
        return result

    try:
        for key, value in overrides.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        bedrock_client.Ledger, bedrock_client.invoke = bound, invoke
        with select_prompts('candidate', 0, lambda amount: check(frozen)):
            yield
    finally:
        bedrock_client.Ledger, bedrock_client.invoke = old_ledger, old_invoke
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def requests(row, arm):
    case = row['case']
    packed = candidate(case['diff']) if arm == 'semantic_bytes' else plan(case['diff'], count_tokens, messages, strategy='baseline')
    result = []
    for index, chunk in enumerate(packed['chunks']):
        for role, focus in FOCUS.items():
            args = (render(chunk['units']), sorted({u.path for u in chunk['units']}), case['context'], anchors(chunk['units']))
            support = '\nSupporting evidence (context only):\n'+chunk['support']+'\nReviewer focus: '+focus
            prompt = reviewer.review_messages(*args, supporting_context=support)
            expected = row['arms'][arm]['assessment_requests'][len(result)]
            if expected['chunk'] != index or expected['role'] != role or expected['messages'] != prompt:
                raise Stopped('execution prompt differs from frozen request')
            result.append((index, role, args, support))
    if len(result) != len(row['arms'][arm]['assessment_requests']):
        raise Stopped('frozen request coverage differs')
    return result


def report(ledger, frozen):
    events = ledger.events()
    measured = ledger.summary()
    rows = []
    for row in frozen['protocol']['rows']:
        if not row['live_eligible']:
            continue
        arms = {}
        for arm in row['order']:
            results = {f"{e['chunk']}:{e['role']}": e['result'] for e in events if e['kind'] == 'assessment_result'
                       and e['case'] == row['case']['id'] and e['arm'] == arm}
            required = [f"{r['chunk']}:{r['role']}" for r in row['arms'][arm]['assessment_requests']]
            arms[arm] = deterministic(results, required)
        rows.append({'id': row['case']['id'], 'expected': row['case']['expected'], 'arms': arms,
                     'matched_complete': all(a['complete'] for a in arms.values())})
    matched = [r for r in rows if r['matched_complete']]
    metrics = {}
    for arm in ('baseline', 'semantic_bytes'):
        calls = [c for c in measured['calls'] if c['context']['arm'] == arm]
        metrics[arm] = {'calls': len(calls), 'charged_nanodollars': sum(c['charged'] or 0 for c in calls),
            'input_tokens': sum((c['metrics'] or {}).get('usage', {}).get('inputTokens', 0) for c in calls),
            'output_tokens': sum((c['metrics'] or {}).get('usage', {}).get('outputTokens', 0) for c in calls),
            'latency_seconds': sum((c['metrics'] or {}).get('latency_seconds', 0) for c in calls),
            'truncations': sum((c['metrics'] or {}).get('stop_reason') == 'max_tokens' for c in calls),
            'unknown_usage': sum(c['charged'] is None for c in calls),
            'matched_case_false_positives': sum(r['expected'] == 'APPROVE' and bool(r['arms'][arm]['findings']) for r in matched),
            'matched_case_misses': sum(r['expected'] == 'REQUEST_CHANGES' and not r['arms'][arm]['findings'] for r in matched),
            'incomplete_cases': sum(not r['arms'][arm]['complete'] for r in rows)}
    diagnostics = [item for e in events if e['kind'] == 'diagnostics' for item in e['events']]
    result = {'experiment': EXPERIMENT, 'protocol_fingerprint': frozen['protocol']['fingerprint'],
        'execution_fingerprint': digest(frozen['execution_sources']),
        'status': next((e['reason'] for e in reversed(events) if e['kind'] == 'stopped'), 'running'),
        'budget': {k: v for k, v in measured.items() if k != 'calls'}, 'matched_complete_cases': len(matched),
        'metrics': metrics, 'cases': rows,
        'invalid_responses': sum(e.get('stage') == 'invalid_answer' or 'validation_error' in e for e in diagnostics),
        'qualification': 'not promoted; finding semantics require adjudication beyond case-level flags'}
    budget.durable_json(ledger.root/'report.json', result)
    return result


def run(ledger, frozen):
    with (ledger.root/'run.lock').open('r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        verify(frozen)
        if any(e['kind'] == 'stopped' and e['reason'] == 'one pass completed' for e in ledger.events()):
            return report(ledger, frozen)
        caller = aws('sts', 'get-caller-identity')
        if caller['Account'] != frozen['account']:
            raise Stopped('account changed before inference')
        ledger.append({'kind': 'identity_verified', 'account': caller['Account'], 'arn': caller['Arn']})
        try:
            started = {(e['case'], e['arm'], e['chunk'], e['role']) for e in ledger.events() if e['kind'] == 'assessment_started'}
            with transport(ledger, frozen):
                # Large dependency cases are the reason for this experiment; run them first.
                rows = sorted(frozen['protocol']['rows'], key=lambda r: (r['case'].get('family') not in
                              ('helper_first', 'helper_last', 'json_records'), frozen['protocol']['rows'].index(r)))
                for row in rows:
                    if not row['live_eligible']:
                        continue
                    for arm in row['order']:
                        for index, role, args, support in requests(row, arm):
                            key = (row['case']['id'], arm, index, role)
                            if key in started:
                                continue
                            ledger.context = dict(zip(('case', 'arm', 'chunk', 'role'), key))
                            ledger.append({'kind': 'assessment_started', **ledger.context})
                            print('starting '+':'.join(map(str, key)), flush=True)
                            with capture() as diagnostics:
                                try:
                                    with select(role):
                                        disposition, findings, summary = reviewer._run_model_reviewer(*args, supporting_context=support)
                                    ledger.append({'kind': 'assessment_result', **ledger.context,
                                        'result': {'disposition': disposition, 'findings': findings, 'summary': summary}})
                                finally:
                                    ledger.append({'kind': 'diagnostics', **ledger.context, 'events': diagnostics})
                        report(ledger, frozen)
                ledger.append({'kind': 'stopped', 'reason': 'one pass completed'})
        except (Stopped, KeyboardInterrupt, OSError, ValueError) as error:
            ledger.append({'kind': 'stopped', 'reason': str(error) or 'interrupted'})
        return report(ledger, frozen)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('prepare', 'run', 'report'))
    parser.add_argument('--protocol', type=Path, default=Path('.cache/packing-quality/frozen.json'))
    args = parser.parse_args()
    if args.command != 'report':
        parser.error('completed research grant is closed; use the offline experiment entry point')
    os.umask(0o077)
    ledger = prepare(json.loads(args.protocol.read_text())) if args.command == 'prepare' else Ledger(state_directory())
    frozen = json.loads((ledger.root/'frozen.json').read_text())
    result = run(ledger, frozen) if args.command == 'run' else report(ledger, frozen)
    print(json.dumps({'directory': str(ledger.root), 'status': result['status'], 'budget': result['budget']}))


if __name__ == '__main__':
    main()
