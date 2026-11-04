"""Prepare review plans and spending authority without invoking a model."""
import json
import os
from pathlib import Path
import subprocess
import sys
import sqlite3
import tempfile
import zipfile

from budget import Ledger, BudgetError, LIMIT, QUALIFICATION_LIMIT
from qualify import cached_qualification
from quote import create, validate

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
    from result_cache import restore as restore_cache
    os.environ['REVIEW_RESULT_CACHE'] = str((DIRECTORY/'cache').resolve())
    restore_cache((DIRECTORY/'previous-caches').glob('*.zip'))
    return ledger


def build_quote(ledger):
    manifests = {}
    for strategy in ('baseline', 'adaptive'):
        output = DIRECTORY/(strategy+'-manifest.json')
        subprocess.run([sys.executable, str(ROOT/'adversarial_review.py'), '--base', os.environ['BASE_SHA'],
                        '--head', os.environ['HEAD_SHA'], '--strategy', strategy, '--dry-run',
                        '--json-output', str(output)], check=True, timeout=2400)
        report = json.loads(output.read_text())
        if report.get('omitted_binary_changes'):
            raise ValueError('unsupported binary changes require manual review')
        manifests[strategy] = report['manifest']
    _, qualification = cached_qualification()
    quote = create(manifests, ledger.summary(), qualification)
    (DIRECTORY/'quote.json').write_text(json.dumps(quote, indent=2)+'\n')
    return quote


def authorize(ledger, quote, approval):
    try:
        validate(approval['quote'])
    except ValueError:
        return 'quote_expired_or_invalid', None
    if approval['quote']['binding'] != quote['binding']:
        return 'quote_stale', None
    grant = approval['grant']
    if (grant['quote_id'] != approval['quote']['id'] or grant['head'] != quote['binding']['head']
            or grant['base'] != quote['binding']['base'] or grant['repository'] != quote['binding']['repository']
            or grant['pr'] != quote['binding']['pr'] or grant['configuration'] != quote['binding']['configuration']
            or grant['run_id'] != int(os.environ['GITHUB_RUN_ID'])):
        return 'quote_stale', None
    if grant['cap_nanodollars'] < quote['cap_nanodollars']:
        return 'approval_below_estimate', None
    try:
        ledger.authorize(grant)
    except BudgetError:
        return 'approval_used', None
    allowance = next(g for g in ledger.summary()['grants'] if g['id'] == grant['id'])
    if allowance['cap_nanodollars']-allowance['committed_nanodollars'] < quote['cap_nanodollars']:
        return 'budget_exhausted', None
    return None, grant['id']


def main():
    DIRECTORY.mkdir(exist_ok=True)
    reason, grant, quote = None, None, None
    ledger = None
    try:
        if os.environ.get('BEDROCK_REVIEW_ENABLED') != 'true':
            reason = 'disabled'
        else:
            ledger = restore()
            summary = ledger.summary()
            if any(c['actual_usd'] is not None and c['actual_usd'] > c['reserved_usd'] for c in summary['calls']):
                raise BudgetError('prior reservation overrun')
            quote = build_quote(ledger)
            approval = DIRECTORY/'approval.json'
            if approval.exists():
                reason, grant = authorize(ledger, quote, json.loads(approval.read_text()))
            elif (quote['cap_nanodollars'] > LIMIT-summary['pilot_committed_nanodollars'] or
                  quote['items'][0]['reserved_nanodollars'] > QUALIFICATION_LIMIT-summary['qualification_committed_nanodollars']):
                reason = 'awaiting_budget'
    except BudgetError:
        reason = 'spending_unresolved'
        quote = None
    except (OSError, ValueError, KeyError, sqlite3.Error, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        reason = 'unsupported_pricing' if str(error) == 'unsupported_pricing' else 'infrastructure_failed'
        quote = None
    report = {'commit_id': os.environ['HEAD_SHA'], 'blocked_reason': reason,
              'quote': quote, 'budget': ledger.summary() if ledger else None}
    (DIRECTORY/'preflight.json').write_text(json.dumps(report, indent=2)+'\n')
    (DIRECTORY/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    if not reason:
        reservation = json.loads((DIRECTORY/'reservation.json').read_text())
        if grant:
            reservation['grant'] = json.loads((DIRECTORY/'approval.json').read_text())['grant']
            reservation['maximum_usd'] = reservation['grant']['cap_nanodollars']/1e9
        (DIRECTORY/'reservation.json').write_text(json.dumps(reservation)+'\n')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f"spend={str(reason is None).lower()}\ngrant={grant or ''}\n")


if __name__ == '__main__':
    main()
