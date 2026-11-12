"""Preserve spending and coverage evidence even when inference is unavailable."""
import json
import os
from pathlib import Path
import subprocess
import sys
import sqlite3

from budget import Ledger, BudgetError
from chunk_plan import ASSESSMENT_LIMIT, bounded_strategy
from reporting import finalize, render, fallback_overview
from qualify import qualify

root = Path(__file__).resolve().parent
path = Path('review-result/report.json')
path.parent.mkdir(exist_ok=True)
head = os.environ['HEAD_SHA']
report = {'commit_id': head, 'complete': False, 'qualified': False,
          'disposition': 'REQUEST_CHANGES', 'findings': [],
          'summary': 'Review unavailable; qualification or assessment did not complete.'}
finalize(report, qualified=False, synthesize=False)
ledger = None
try:
    if os.environ.get('BEDROCK_REVIEW_ENABLED') != 'true':
        raise ValueError('bedrock review is disabled pending model access and qualification')
    from preflight import restore
    preflight = json.loads(Path('review-result/preflight.json').read_text())
    if preflight['blocked_reason'] or preflight['commit_id'] != head:
        raise ValueError('review preflight is not authorized')
    if os.environ.get('REVIEW_COST_POLICY') != 'true':
        raise ValueError('repository cost policy required')
    ledger = restore()
    from quote import validate, configuration
    from cost_policy import check
    validate(preflight['quote'])
    binding = preflight['quote']['binding']
    if (binding['head'] != head or binding['base'] != os.environ['BASE_SHA']
            or binding['repository'] != os.environ['GITHUB_REPOSITORY']
            or binding['pr'] != int(os.environ['PR_NUMBER']) or binding['configuration'] != configuration()):
        raise ValueError('review plan changed')
    check(preflight['quote']['projected_nanodollars'], ledger.policy_budget())
    starting_cost = ledger.summary()['committed_usd']
    baseline = json.loads(Path('review-result/baseline-manifest.json').read_text())
    for field in ('files', 'total_lines', 'total_chunks', 'total_units', 'excluded_assets', 'test_evidence'):
        report[field] = baseline[field]
    report['overview'] = fallback_overview(report)
    qualification = qualify('review-result/qualification.json')
    if not qualification['qualified']:
        report['blocked_reason'] = 'qualification_failed'
        report['summary'] = 'Live model qualification failed; PR assessment did not start.'
        for case in qualification['cases']:
            if not case['passed']:
                report['review_failures'].append('qualification case failed: '+case['case']+'; '+case['summary'])
        for strategy, comparison in qualification['comparison'].items():
            for batch in comparison['batches']:
                if not batch['passed']:
                    report['review_failures'].append(f"{strategy} qualification failed: missing defects {batch['missing_defects']}; unexpected findings {batch['unexpected_findings']}")
                    for chunk in batch['report'].get('chunks', []):
                        if not chunk.get('complete'):
                            report['review_failures'].append(chunk['summary'])
        raise ValueError('model qualification or baseline comparison did not pass')
    manifests = {s: json.loads(Path(f'review-result/{s}-manifest.json').read_text())['manifest']
                 for s in ('baseline', 'adaptive')}
    strategy = bounded_strategy(qualification['strategy'], manifests)
    Path('review-result/strategy.json').write_text(json.dumps({
        'qualified_preference': qualification['strategy'], 'selected': strategy,
        'assessment_limit': ASSESSMENT_LIMIT}))
    path.unlink(missing_ok=True)
    subprocess.run([sys.executable, str(root/'adversarial_review.py'), '--base', os.environ['BASE_SHA'],
                    '--head', head, '--strategy', strategy, '--fail-on', 'none',
                    '--json-output', str(path)], timeout=2400, check=False)
    if not path.exists():
        raise ValueError('review did not produce a report')
    report = json.loads(path.read_text())
except (OSError, ValueError, KeyError, StopIteration, sqlite3.Error, BudgetError, subprocess.SubprocessError) as error:
    report['blocked_reason'] = report.get('blocked_reason', 'spending_unresolved' if isinstance(error, BudgetError) else 'infrastructure_failed')
    report['review_failures'].append('review execution unavailable: '+type(error).__name__)
finally:
    if ledger is not None:
        report['budget'] = ledger.summary()
        Path('review-result/settled.json').write_text(json.dumps(report['budget'], indent=2)+'\n')
    from result_cache import measurements
    report['usage'] = measurements()
    if ledger is not None and 'starting_cost' in globals():
        report['usage']['incremental_committed_usd'] = ledger.summary()['committed_usd']-starting_cost
    blocked = Path('review-result/budget-blocked.json')
    if blocked.exists():
        report['blocked_reason'] = json.loads(blocked.read_text())['reason']
        if report['blocked_reason'] == 'budget_exhausted':
            from preflight import build_quote
            try:
                report['quote'] = build_quote(ledger)
                report['blocked_reason'] = report['quote']['blocked_reason'] or 'budget_exhausted'
            except (OSError, ValueError, subprocess.SubprocessError):
                report['blocked_reason'] = 'infrastructure_failed'
    report['body'] = render(report)
    path.write_text(json.dumps(report, indent=2)+'\n')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        Path(os.environ['GITHUB_STEP_SUMMARY']).write_text(report['body'])
