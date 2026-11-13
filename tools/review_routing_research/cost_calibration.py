"""Manual cost calibration; no production qualification or automatic promotion."""
from contextlib import contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

from live_packing import environment, sources, transport, Stopped
import adversarial_review as reviewer
from bedrock_client import estimate_input_units
from diagnostics import capture
from egm import FOCUS
from evidence import locations
from model_catalog import ROLES, cost, digest, manifest, routing_override, select
from models import account, aws
from quote import estimate
from research_cases import cases
from research_reporting import deterministic

EXPERIMENT = 'review-cost-calibration-v1'
CAP = 1_000_000_000  # nanodollars: $1 maximum for this manual trial
ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('calibration_budget', ROOT.parent/'adversarial_reviewer/research_ledger.py')
budget = importlib.util.module_from_spec(spec)
spec.loader.exec_module(budget)
budget.EXPERIMENT, budget.CAP = EXPERIMENT, CAP


def directory():
    common = subprocess.run(['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                            capture_output=True, text=True, check=True).stdout.strip()
    return Path(common)/'reviewer-research'/EXPERIMENT


@contextmanager
def candidate(name):
    token = routing_override.set({'schema': 1, 'roles': {r: name for r in ROLES}})
    try:
        yield
    finally:
        routing_override.reset(token)


def request(case, role):
    anchors = [a for a in locations(case['diff']).values() if a['evidence'].strip()]
    args = (case['diff'], case['files'], case['context'], anchors)
    support = '\nReviewer focus: '+FOCUS[role]
    return args, support, reviewer.review_messages(*args, supporting_context=support)


def freeze(name):
    environment()
    with candidate(name):
        routing = manifest()
        if any(m['adapter'] != 'converse' for m in routing['models'].values()):
            raise Stopped('calibration transport supports converse candidates only')
        fixtures = [c for c in cases() if c['source'] == 'new']
        prompts = {c['id']: {r: request(c, r)[2] for r in FOCUS} for c in fixtures}
        if any(estimate_input_units(p) > 36000 for roles in prompts.values() for p in roles.values()):
            raise Stopped('calibration fixture exceeds verification headroom')
        items = estimate({'chunks': [{'input_tokens': max(estimate_input_units(p) for p in roles.values())}
                                     for roles in prompts.values()]})
        frozen = {'schema': 1, 'experiment': EXPERIMENT, 'cap_nanodollars': CAP,
                  'paid_authorized': False, 'candidate': name, 'profile': os.environ['AWS_PROFILE'],
                  'region': os.environ['AWS_REGION'], 'account': account(), 'routing': routing,
                  'execution_sources': sources(), 'cases': fixtures, 'assessment_prompts': prompts,
                  'whole_plan_reserved_nanodollars': sum(i['reserved_nanodollars'] for i in items),
                  'whole_plan_estimated_nanodollars': sum(i['estimated_nanodollars'] for i in items)}
    frozen['fingerprint'] = digest(frozen)
    return frozen


def verify(frozen):
    environment()
    if (frozen['fingerprint'] != digest({k: v for k, v in frozen.items() if k != 'fingerprint'})
            or frozen['experiment'] != EXPERIMENT or frozen['cap_nanodollars'] != CAP
            or frozen['profile'] != os.environ['AWS_PROFILE'] or frozen['region'] != os.environ['AWS_REGION']
            or frozen['account'] != account() or frozen['routing'] != manifest()
            or frozen['execution_sources'] != sources()):
        raise Stopped('frozen calibration changed; retain history and stop')


def report(ledger, frozen):
    from experiment import grade
    events, measured = ledger.events(), ledger.summary()
    calls = measured['calls']
    requests = {e['call_id']: e['request'] for e in events if e['kind'] == 'transport_started'}
    priced = []
    for call in calls:
        payload = requests.get(call['id'])
        units = None
        if payload:
            texts = payload['system'] + [t for m in payload['messages'] for t in m['content']]
            units = estimate_input_units([{'content': t['text']} for t in texts])
        priced.append({'id': call['id'], 'context': call['context'], 'reserved_nanodollars': call['reserved'],
                       'request_input_units': units,
                       'request_estimated_nanodollars': cost(call['model'], units, call['outputs']) if units is not None else None,
                       'usage_priced_nanodollars': call['charged'], 'metrics': call['metrics']})
    rows = []
    for case in frozen['cases']:
        results = {e['role']: e['result'] for e in events if e['kind'] == 'calibration_result' and e['case'] == case['id']}
        result = deterministic(results, list(FOCUS))
        expected = (case['expected'] == 'REQUEST_CHANGES') if case['oracle']['resolved'] else None
        # Model findings need independent behavior and source-location adjudication.
        result['grade'] = grade(expected_defect=expected, complete=result['complete'], findings=result['findings'],
                                adjudications=[{} for _ in result['findings']])
        rows.append({'case': case['id'], 'expected': case['expected'], 'oracle': case['oracle'], **result})
    diagnostics = [d for e in events if e['kind'] == 'calibration_diagnostics' for d in e['events']]
    matched = [c for c in priced if c['usage_priced_nanodollars'] is not None and c['request_estimated_nanodollars'] is not None]
    actual = sum(c['usage_priced_nanodollars'] for c in matched)
    result = {'experiment': EXPERIMENT, 'production_qualified': False, 'fingerprint': frozen['fingerprint'],
              'status': next((e['reason'] for e in reversed(events) if e['kind'] == 'calibration_stopped'), 'prepared'),
              'budget': {k: v for k, v in measured.items() if k != 'calls'},
              'whole_plan_reserved_nanodollars': frozen['whole_plan_reserved_nanodollars'],
              'whole_plan_estimated_nanodollars': frozen['whole_plan_estimated_nanodollars'],
              'matched_calls': len(matched), 'matched_usage_priced_nanodollars': actual,
              'matched_request_estimated_nanodollars': sum(c['request_estimated_nanodollars'] for c in matched),
              'matched_reserved_nanodollars': sum(c['reserved_nanodollars'] for c in matched),
              'matched_estimate_to_usage_ratio': sum(c['request_estimated_nanodollars'] for c in matched)/actual if actual else None,
              'matched_reservation_to_usage_ratio': sum(c['reserved_nanodollars'] for c in matched)/actual if actual else None,
              'invocations': len(requests), 'unknown_usage': sum(c['charged'] is None for c in calls),
              'input_tokens': sum((c['metrics'] or {}).get('usage', {}).get('inputTokens', 0) for c in calls),
              'output_tokens': sum((c['metrics'] or {}).get('usage', {}).get('outputTokens', 0) for c in calls),
              'latency_seconds': sum((c['metrics'] or {}).get('latency_seconds', 0) for c in calls),
              'truncations': sum((c['metrics'] or {}).get('stop_reason') == 'max_tokens' for c in calls),
              'invalid_responses': sum(d.get('stage') == 'invalid_answer' or 'validation_error' in d for d in diagnostics),
              'abstentions': sum(bool(d['response'].get('abstention')) for d in diagnostics if isinstance(d.get('response'), dict)),
              'incomplete_cases': sum(not r['complete'] for r in rows),
              'complete_case_flag_misses': sum(r['complete'] and r['oracle']['resolved'] and r['expected'] == 'REQUEST_CHANGES' and not r['findings'] for r in rows),
              'complete_benign_case_flags': sum(r['complete'] and r['oracle']['resolved'] and r['expected'] == 'APPROVE' and bool(r['findings']) for r in rows),
              'calls': priced, 'cases': rows,
              'interpretation': 'Request estimates price UTF-8 byte allowances and bounded output, not predicted tokens. Compare matched calls only. Usage-priced cost is not invoice reconciliation. Findings remain unadjudicated.'}
    budget.durable_json(ledger.root/'report.json', result)
    return result


def run(frozen, approval, *, initialize=False):
    if approval != frozen['fingerprint']:
        raise Stopped('manual approval must match the frozen fingerprint and $1 ceiling')
    with candidate(frozen['candidate']):
        verify(frozen)
        ledger = budget.ResearchLedger.initialize(directory(), frozen) if initialize else budget.ResearchLedger(directory())
        if json.loads((ledger.root/'frozen.json').read_text()) != frozen:
            raise Stopped('conflicting calibration history')
        with (ledger.root/'run.lock').open('r+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if any(e['kind'] == 'calibration_stopped' and e['reason'] == 'one pass completed' for e in ledger.events()):
                return report(ledger, frozen)
            # Outstanding usage is retained, and automatic retries after interruption
            # are forbidden until the uncertain call has been reconciled.
            if ledger.summary()['outstanding_nanodollars']:
                raise Stopped('unresolved calibration calls; retain reservations and reconcile')
            ledger.append({'kind': 'manual_authorization', 'fingerprint': approval, 'cap_nanodollars': CAP})
            caller = aws('sts', 'get-caller-identity')
            if caller['Account'] != frozen['account']:
                raise Stopped('account changed before inference')
            ledger.append({'kind': 'identity_verified', 'account': caller['Account'], 'arn': caller['Arn']})
            started = {(e['case'], e['role']) for e in ledger.events() if e['kind'] == 'calibration_started'}
            try:
                with transport(ledger, frozen, check=verify, experiment=EXPERIMENT):
                    for case in frozen['cases']:
                        for role in FOCUS:
                            if (case['id'], role) in started:
                                continue
                            args, support, messages = request(case, role)
                            if messages != frozen['assessment_prompts'][case['id']][role]:
                                raise Stopped('assessment prompt changed')
                            ledger.context = {'case': case['id'], 'role': role}
                            ledger.append({'kind': 'calibration_started', **ledger.context})
                            with capture() as events, select(role):
                                disposition, findings, summary = reviewer._run_model_reviewer(*args,
                                    deadline=time.monotonic()+reviewer.ASSESSMENT_SECONDS, supporting_context=support)
                            ledger.append({'kind': 'calibration_diagnostics', **ledger.context, 'events': events})
                            ledger.append({'kind': 'calibration_result', **ledger.context,
                                           'result': {'disposition': disposition, 'findings': findings, 'summary': summary}})
                            report(ledger, frozen)
                ledger.append({'kind': 'calibration_stopped', 'reason': 'one pass completed'})
            except BaseException as error:
                ledger.append({'kind': 'calibration_stopped', 'reason': type(error).__name__+': '+str(error)})
                raise
            finally:
                result = report(ledger, frozen)
            return result
