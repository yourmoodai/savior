"""Freeze and run one synthetic reviewer comparison under a persistent $4 ceiling."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

import adversarial_review as reviewer
import bedrock_client
import egm
import model_catalog
import reporting
from diagnostics import capture
from prompt_profiles import select as select_prompts
from research_cases import cases
from research_ledger import CAP, EXPERIMENT, ResearchLedger, ResearchStopped, durable_json
from research_reporting import deterministic, markdown, report

ROOT = Path(__file__).resolve().parent
GENERAL_FOCUS = 'Review correctness, security, privacy, authorization, compatibility and regression behavior together. '
GENERAL_FOCUS += 'Cover every changed line and every supplied repository invariant.'


def state_directory():
    common = subprocess.run(['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                            check=True, capture_output=True, text=True).stdout.strip()
    return Path(common) / 'reviewer-research' / EXPERIMENT


def configuration():
    paths = sorted(p for p in ROOT.glob('*.py') if not p.name.startswith('test_'))
    paths += [ROOT / 'models.json', ROOT / 'routing.json', ROOT / 'iam/trust.json']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def packing(diff):
    from chunk_plan import anchors, manifest, plan, render
    from quote import estimate, qualification_estimate

    def messages(units, support):
        return reviewer.review_messages(render(units), sorted({u.path for u in units}), '', anchors(units),
                                        supporting_context=support)

    alternatives = {}
    for strategy in ('baseline', 'adaptive'):
        planned = plan(diff, lambda m: bedrock_client.count_tokens(m, float('inf')), messages, strategy=strategy)
        described = manifest(planned)
        items = estimate(described)
        roles = [item for item in items if item['phase'].split()[0] in egm.FOCUS]
        single = [item for item in items if item['phase'].split()[0] == 'correctness']
        alternatives[strategy] = {
            'manifest': described,
            'primary_evidence': sorted((a['file'], a.get('side', 'new'), a['line'], a['evidence'])
                                       for c in planned['chunks'] for a in anchors(c['units'])),
            'reserved_nanodollars': {
                'current': sum(i['reserved_nanodollars'] for i in items),
                'three_role': sum(i['reserved_nanodollars'] for i in roles),
                'single_role': sum(i['reserved_nanodollars'] for i in single)},
        }
    return {'diff_sha256': hashlib.sha256(diff.encode()).hexdigest(), 'plans': alternatives,
            'equivalent_primary_coverage': alternatives['baseline']['primary_evidence'] == alternatives['adaptive']['primary_evidence'],
            'qualification_reserved_nanodollars': qualification_estimate()['reserved_nanodollars'],
            'measurement': 'offline worst-case reservation scenarios; not charges or quality results'}


def freeze(replay=None):
    routing = model_catalog.manifest()
    if set(routing['routing']['roles'].values()) != {'deepseek_v3_2'}:
        raise ResearchStopped('research requires the current deepseek routing; no substitute model')
    profile = os.environ.get('AWS_PROFILE')
    if profile not in ('awill', 'yourmood-prod') or os.environ.get('AWS_REGION') != 'us-east-1':
        raise ResearchStopped('authorized aws profile and us-east-1 must be configured')
    fixtures = cases()
    from evidence import locations
    prompts = {}
    for case in fixtures:
        evidence = [a for a in locations(case['diff']).values() if a['evidence'].strip()]
        prompts[case['id']] = {role: reviewer.review_messages(case['diff'], case['files'], case['context'], evidence,
                              supporting_context='\nReviewer focus: ' + focus)
                              for role, focus in {**egm.FOCUS, 'general': GENERAL_FOCUS}.items()}
    from models import account
    from runtime_facts import runtime_facts
    result = {'schema': 1, 'experiment': EXPERIMENT, 'cap_nanodollars': CAP,
              'authorization': 'explicit research-only exemption; production PR and daily limits unchanged',
              'profile': profile, 'region': 'us-east-1', 'account': account(),
              'configuration': configuration(), 'routing': routing, 'cases': fixtures,
              'roles': list(egm.FOCUS), 'assessment_prompts': prompts,
              'prompt_profile': 'candidate', 'temperature': 0,
              'runtime_facts': runtime_facts(),
              'python_version': platform.python_version(),
              'execution_order': {case['id']: ['current', 'single_role'] if index % 2 == 0 else ['single_role', 'current']
                                  for index, case in enumerate(fixtures)},
              'packing': {'synthetic': packing(''.join(c['diff'] for c in fixtures))}}
    if replay is not None:
        result['packing']['replay'] = packing(replay)
    return result


def verify_frozen(frozen):
    from runtime_facts import runtime_facts
    if frozen['configuration'] != configuration() or frozen['routing'] != model_catalog.manifest():
        raise ResearchStopped('configuration changed after freezing; preserve this ledger and stop')
    if frozen['runtime_facts'] != runtime_facts() or frozen['python_version'] != platform.python_version():
        raise ResearchStopped('runtime evidence changed after freezing')
    if os.environ.get('AWS_PROFILE') != frozen['profile'] or os.environ.get('AWS_REGION') != frozen['region']:
        raise ResearchStopped('aws routing differs from the frozen experiment')
    if any(os.environ.get(k) for k in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN')):
        raise ResearchStopped('ambient credentials conflict with the frozen sso profile')
    if any(k.startswith('AWS_ENDPOINT_URL') and v for k, v in os.environ.items()):
        raise ResearchStopped('aws endpoint override conflicts with the frozen routing')
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        raise ResearchStopped('research exemption cannot run in production ci')


@contextmanager
def research_transport(ledger, frozen, check=verify_frozen):
    """Scope the research ledger to this process; reuse the production completion path."""
    original_ledger, original_invoke = bedrock_client.Ledger, bedrock_client.invoke
    original_assess = reviewer._run_model_reviewer
    overrides = {'BEDROCK_REVIEW_ENABLED': 'true', 'REVIEW_COST_POLICY': 'false',
                 'REVIEW_BUDGET_GRANT': EXPERIMENT, 'REVIEW_BUDGET_LEDGER': str(ledger.path),
                 'REVIEW_BUDGET_PHASE': 'review', 'REVIEW_CACHE_DISABLED': 'true',
                 'REVIEW_RESULT_CACHE': None}
    previous = {key: os.environ.get(key) for key in overrides}

    def bound_ledger(path):
        if Path(path) != ledger.path:
            raise ResearchStopped('research ledger path changed')
        return ledger

    def before_call(amount):
        check(frozen)

    def invoke(operation, payload, deadline):
        call = ledger.calls(ledger.events())[-1]
        context = {**ledger.context, 'call_id': call['id'], 'role': call['role'],
                   'purpose': call['identity'].split(':', 1)[0]}
        ledger.append({'kind': 'transport_started', 'context': context, 'request': payload})
        started = time.monotonic()
        try:
            response = original_invoke(operation, payload, deadline)
        except BaseException as error:
            ledger.append({'kind': 'transport_failed', 'context': context,
                           'seconds': time.monotonic() - started, 'error_type': type(error).__name__})
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise ResearchStopped('inference transport failed; reservation retained; no routing fallback') from error
        ledger.append({'kind': 'transport_response', 'context': context,
                       'seconds': time.monotonic() - started, 'response': response})
        return response

    def assess(*args, **kwargs):
        if kwargs.get('candidate') is not None:
            return original_assess(*args, **kwargs)
        role = 'general' if ledger.context['arm'] == 'single_role' else model_catalog.active_role.get()
        result = original_assess(*args, **kwargs)
        ledger.append({'kind': 'role_result', **ledger.context, 'role': role,
                       'result': {'disposition': result[0], 'findings': result[1], 'summary': result[2]}})
        return result

    try:
        for key, value in overrides.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        bedrock_client.Ledger, bedrock_client.invoke = bound_ledger, invoke
        reviewer._run_model_reviewer = assess
        with select_prompts('candidate', 0, before_call):
            yield
    finally:
        bedrock_client.Ledger, bedrock_client.invoke = original_ledger, original_invoke
        reviewer._run_model_reviewer = original_assess
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def current(case):
    disposition, findings, summary = reviewer.run_model_reviewer(case['diff'], case['files'], case['context'])
    complete = disposition != 'COMMENT' and not summary.startswith('review incomplete:')
    result = {'disposition': disposition, 'findings': findings, 'summary': summary,
              'complete': complete, 'files': len(case['files']), 'total_chunks': 1,
              'completed_chunks': int(complete), 'excluded_assets': [], 'omitted_binary_changes': [],
              'chunks': [{'index': 1, 'file': case['files'][0], 'disposition': disposition,
                          'complete': complete, 'summary': summary}],
              'qualification_assumed_for_research': True, 'synthesis_complete': False}
    try:
        result['overview'] = reporting.synthesize_overview(result)
        result['synthesis_complete'] = True
    except ValueError as error:
        result['overview_error'] = str(error)
    result['complete'] = complete and result['synthesis_complete']
    result['production_disposition'] = 'APPROVE' if result['complete'] and disposition == 'APPROVE' and not findings else 'REQUEST_CHANGES'
    # Production finalize requires both successful prose stages for approval.
    # Research distinguishes that failure from an observed defect.
    if not result['complete']:
        result['disposition'] = 'COMMENT' if not findings else 'REQUEST_CHANGES'
    return result


def single(case):
    with model_catalog.select('correctness'):
        disposition, findings, summary = reviewer._run_model_reviewer(
            case['diff'], case['files'], case['context'], supporting_context='\nReviewer focus: ' + GENERAL_FOCUS)
    return deterministic({'general': {'disposition': disposition, 'findings': findings, 'summary': summary}}, ['general'])


def write_report(ledger, frozen):
    result = report(frozen, ledger)
    durable_json(ledger.root / 'report.json', result)
    (ledger.root / 'report.md').write_text(markdown(result))
    return result


def run(ledger, frozen):
    from models import aws
    with (ledger.root / 'run.lock').open('r+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ResearchStopped('another research runner holds the experiment lock') from None
        try:
            verify_frozen(frozen)
            caller = aws('sts', 'get-caller-identity')
            if caller['Account'] != frozen['account']:
                raise ResearchStopped('aws account differs from frozen trust boundary')
            ledger.append({'kind': 'identity_verified', 'account': caller['Account'], 'arn': caller['Arn']})
            with research_transport(ledger, frozen):
                started = {(e['case'], e['arm']) for e in ledger.events() if e['kind'] == 'step_started'}
                for case in frozen['cases']:
                    for arm in frozen['execution_order'][case['id']]:
                        if (case['id'], arm) in started:
                            continue
                        ledger.context = {'case': case['id'], 'arm': arm}
                        ledger.append({'kind': 'step_started', **ledger.context})
                        print(f"starting {case['id']}: {arm}", flush=True)
                        with capture() as events:
                            try:
                                result = current(case) if arm == 'current' else single(case)
                                ledger.append({'kind': 'step_result', **ledger.context, 'result': result})
                            finally:
                                ledger.append({'kind': 'diagnostics', **ledger.context, 'events': events})
                                write_report(ledger, frozen)
                ledger.append({'kind': 'stopped', 'reason': 'one pass completed'})
        except (ResearchStopped, KeyboardInterrupt, ValueError, OSError, subprocess.SubprocessError) as error:
            ledger.append({'kind': 'stopped', 'reason': str(error) or 'interrupted'})
        finally:
            result = write_report(ledger, frozen)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run', 'report'))
    parser.add_argument('--replay-diff', type=Path, help='offline reservation replay only; never sent to inference')
    args = parser.parse_args()
    if args.command != 'report':
        parser.error('completed research grant is closed; use the offline experiment entry point')
    os.umask(0o077)
    root = state_directory()
    if args.command == 'prepare':
        if root.exists() or root.with_suffix('.anchor.json').exists():
            parser.error('research identity already exists; do not recreate missing history')
        frozen = freeze(args.replay_diff.read_text() if args.replay_diff else None)
        ledger = ResearchLedger.initialize(root, frozen)
        result = write_report(ledger, frozen)
    else:
        if args.replay_diff:
            parser.error('replay inputs must be frozen during prepare')
        ledger = ResearchLedger(root)
        frozen = json.loads((root / 'frozen.json').read_text())
        result = run(ledger, frozen) if args.command == 'run' else write_report(ledger, frozen)
    print(json.dumps({'directory': str(root), 'status': result['status'],
                      'charged_nanodollars': result['budget']['charged_nanodollars'],
                      'outstanding_nanodollars': result['budget']['outstanding_nanodollars']}))
    return 2 if args.command == 'run' and result['status'] not in (
        'one pass completed', 'next full-context reservation exceeds research ceiling') else 0


if __name__ == '__main__':
    sys.exit(main())
