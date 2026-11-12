"""Explicitly authorized synthetic investigation using one persistent spending cap."""
import argparse
import json
import os
from pathlib import Path
import uuid

from budget import Ledger
from model_catalog import ROLES, manifest, routing_override, select
from models import check_account
from quote import configuration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--cap-nanodollars', type=int, required=True)
    parser.add_argument('--phase', choices=('reproduce', 'qualify'), required=True)
    parser.add_argument('--case-index', type=int, default=0)
    parser.add_argument('--role', choices=('correctness', 'security', 'compatibility'), default='compatibility')
    args = parser.parse_args()
    from evaluate_model import CASES
    if not 0 <= args.case_index < len(CASES):
        parser.error('invalid synthetic case index')
    root = args.directory.resolve()
    if not (root/'ledger.db').is_file():
        parser.error('existing investigation ledger required; cannot reset history')
    os.umask(0o077)
    check_account()
    ledger = Ledger(root/'ledger.db')
    ledger.set_ceiling(args.cap_nanodollars)
    grant = 'gpt-oss-investigation'
    ledger.authorize({'id': grant, 'cap_nanodollars': args.cap_nanodollars,
                      'purpose': 'authorized synthetic diagnostic iterations'})
    os.environ.update(REVIEW_BUDGET_LEDGER=str(ledger.path), REVIEW_BUDGET_GRANT=grant,
                      REVIEW_BUDGET_PHASE='qualification', REVIEW_CACHE_DISABLED='true',
                      REVIEW_RESULT_CACHE='', BEDROCK_REVIEW_ENABLED='true')
    routing_override.set({'schema': 1, 'roles': {r: 'gpt_oss_20b' for r in ROLES}})
    attempt = root/('attempt-'+uuid.uuid4().hex)
    attempt.mkdir(mode=0o700)
    (attempt/'configuration.json').write_text(json.dumps({'configuration': configuration(),
        'routing': manifest(), 'phase': args.phase, 'case_index': args.case_index, 'role': args.role,
        'ceiling_nanodollars': ledger.summary()['ceiling_nanodollars']}))
    print(str(attempt), flush=True)
    if args.phase == 'qualify':
        from qualify import qualify
        result = qualify(attempt/'qualification.json')
    else:
        from diagnostics import capture
        from adversarial_review import _run_model_reviewer
        from qualify import fixture
        from egm import FOCUS
        diff, filename = fixture(args.case_index, CASES[args.case_index])
        with capture() as events, select(args.role):
            actual, findings, summary = _run_model_reviewer(diff, [filename], '',
                supporting_context='\nReviewer focus: '+FOCUS[args.role])
        result = {'qualified': False, 'actual': actual, 'findings': findings,
                  'summary': summary, 'diagnostics': events}
    result['budget'] = ledger.summary()
    (attempt/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'qualified': result.get('qualified'), 'actual': result.get('actual'),
                     'summary': result.get('summary'), 'committed_usd': result['budget']['committed_usd']}))


if __name__ == '__main__':
    main()
