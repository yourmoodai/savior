"""Prepare review plans and spending authority without invoking a model."""
import json
import os
from pathlib import Path
import subprocess
import sys
import sqlite3
import tempfile
import zipfile

from budget import Ledger, BudgetError
from qualify import cached_qualification
from quote import create, apply_policy
from review_plan import PlanningError, require_feasible, feasible

ROOT = Path(__file__).parent
DIRECTORY = Path('review-result')


def restore():
    try:
        return restore_history()
    except (OSError, ValueError, KeyError, sqlite3.Error, zipfile.BadZipFile) as error:
        raise BudgetError('spending history is unavailable or invalid') from error


def restore_history():
    os.environ['REVIEW_BUDGET_LEDGER'] = str((DIRECTORY/'ledger.db').resolve())
    ledger = Ledger(os.environ['REVIEW_BUDGET_LEDGER'])
    archives = list((DIRECTORY/'previous-ledgers').glob('*.zip'))
    if (DIRECTORY/'previous-ledger.zip').exists():
        archives.append(DIRECTORY/'previous-ledger.zip')
    for archive in archives:
        with zipfile.ZipFile(archive) as zipped:
            if zipped.getinfo('ledger.db').file_size > 10_000_000:  # 10 MB ledger limit
                raise BudgetError('pilot ledger exceeds size limit')
            with tempfile.NamedTemporaryFile(suffix='.db') as source:
                source.write(zipped.read('ledger.db')); source.flush()
                ledger.merge(source.name)
    for recovery in json.loads((DIRECTORY/'recoveries.json').read_text()):
        ledger.record_recovery(f"{recovery['run']}-{recovery['attempt']}", recovery['nanodollars'])
    ledger.record_preflight()
    ledger.record_external_reservation(int(os.environ.get('BEDROCK_EXTERNAL_PROBE_NANODOLLARS') or '0'))
    from history import attribute
    attribute(ledger)
    from result_cache import restore as restore_cache
    os.environ['REVIEW_RESULT_CACHE'] = str((DIRECTORY/'cache').resolve())
    restore_cache((DIRECTORY/'previous-caches').glob('*.zip'))
    return ledger


def build_quote(ledger):
    manifests = {}
    for strategy in ('baseline', 'adaptive'):
        output = DIRECTORY/(strategy+'-manifest.json')
        output.unlink(missing_ok=True)
        result = subprocess.run([sys.executable, str(ROOT/'adversarial_review.py'), '--base', os.environ['BASE_SHA'],
                        '--head', os.environ['HEAD_SHA'], '--strategy', strategy, '--dry-run',
                        '--json-output', str(output)], check=False, timeout=2400)
        report = json.loads(output.read_text())
        if report.get('blocked_reason') == 'planning_failed':
            raise PlanningError(report['planning_failure'])
        # Exit 2 preserves an infeasible plan for diagnostics. Try both
        # strategies before deciding whether any complete plan is admissible.
        if not (result.returncode == 2 and not feasible(report.get('manifest', {}))):
            result.check_returncode()
        if report.get('omitted_binary_changes'):
            raise ValueError('unsupported binary changes require manual review')
        manifests[strategy] = report['manifest']
    require_feasible(manifests)
    _, qualification = cached_qualification()
    quote = apply_policy(create(manifests, ledger.summary(), qualification), ledger.policy_budget())
    (DIRECTORY/'quote.json').write_text(json.dumps(quote, indent=2)+'\n')
    return quote


def main():
    DIRECTORY.mkdir(exist_ok=True)
    reason, quote = None, None
    ledger = None
    try:
        if os.environ.get('BEDROCK_REVIEW_ENABLED') != 'true':
            reason = 'disabled'
        else:
            ledger = restore()
            ledger.policy_budget()
            summary = ledger.summary()
            if any(c['actual_usd'] is not None and c['actual_usd'] > c['reserved_usd'] for c in summary['calls']):
                raise BudgetError('prior reservation overrun')
            quote = build_quote(ledger)
            reason = quote['blocked_reason']
    except PlanningError as error:
        reason = 'planning_failed'
        (DIRECTORY/'planning-failure.json').write_text(json.dumps({'reason': reason, 'details': str(error)})+'\n')
    except BudgetError:
        reason = 'spending_unresolved'
        quote = None
    except (OSError, ValueError, KeyError, sqlite3.Error, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        reason = 'unsupported_pricing' if str(error) == 'unsupported_pricing' else 'infrastructure_failed'
        quote = None
    report = {'commit_id': os.environ['HEAD_SHA'], 'blocked_reason': reason,
              'quote': quote, 'budget': ledger.summary() if ledger else None}
    if reason == 'planning_failed':
        report['planning_failure'] = json.loads((DIRECTORY/'planning-failure.json').read_text())['details']
    (DIRECTORY/'preflight.json').write_text(json.dumps(report, indent=2)+'\n')
    (DIRECTORY/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    if not reason:
        reservation = json.loads((DIRECTORY/'reservation.json').read_text())
        reservation.update(repository=quote['binding']['repository'], pr=quote['binding']['pr'],
                           utc_day=quote['policy']['utc_day'], projected_nanodollars=quote['projected_nanodollars'],
                           maximum_usd=min(quote['policy']['pr_remaining_nanodollars'],
                                           quote['policy']['daily_remaining_nanodollars'])/1e9)
        (DIRECTORY/'reservation.json').write_text(json.dumps(reservation)+'\n')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f"spend={str(reason is None).lower()}\n")


if __name__ == '__main__':
    main()
