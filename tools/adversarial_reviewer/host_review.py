"""Maintainer host entry point for offline replay, an exact PR quote, and explicit execution."""
import argparse
from contextlib import chdir
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from host_binding import REPOSITORY, ROLE, check_deployment, checked_pr, check_revision, output
from quote import configuration, digest, estimate, qualification_identity, validate
from review_plan import admit, feasible

TOOLS = Path(__file__).resolve().parent
STATE = Path.home()/'Library'/'Application Support'/'ai.yourmood.savior.bedrock'
STRATEGIES = ('baseline','adaptive')


def read(path):
    return json.loads(Path(path).read_text())


def estimates(manifests):
    return {s:{'chunks':len(m['chunks']), 'estimated_usd':sum(i['estimated_nanodollars'] for i in estimate(m))/1e9}
            for s,m in manifests.items() if feasible(m)}


def manifests():
    return {s:read(Path('review-result')/(s+'-manifest.json'))['manifest'] for s in STRATEGIES}


def replay(base, head):
    with tempfile.TemporaryDirectory() as dest:
        subprocess.run([sys.executable,str(TOOLS/'admission.py'),'--base',base,'--head',head,'--output',dest],check=True)
        return {'base':base,'head':head,'assessment_plans':estimates({
            s:read(Path(dest)/(s+'.json'))['manifest'] for s in STRATEGIES}),
            'remaining_allowance':'requires authoritative ledger'}


def fetch_objects(number):
    cache = STATE/'objects.git'
    subprocess.run(['git','init','--quiet','--bare','--template=',str(cache)], check=True)
    subprocess.run(['git','--git-dir='+str(cache),'-c','core.hooksPath=/dev/null','fetch','--no-tags',
                    '--no-recurse-submodules','https://github.com/'+REPOSITORY+'.git',
                    'refs/heads/main:refs/heads/main','+refs/pull/'+str(number)+'/head:refs/pulls/'+str(number)],
                   check=True, timeout=300)
    os.environ['GIT_DIR'] = str(cache)


def command(number, quote_id):
    if (STATE/'aws-config').is_file():
        os.environ.update(AWS_CONFIG_FILE=str(STATE/'aws-config'),AWS_PROFILE=ROLE)
        if os.environ.get('AWS_REGION') != 'us-east-1':
            raise ValueError('configure the review region us-east-1')
    pr = checked_pr(number, live=bool(quote_id))
    base, head = pr['base']['sha'], pr['head']['sha']
    check_deployment(base)
    os.umask(0o077)
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    fetch_objects(number)
    os.environ.update(GITHUB_REPOSITORY=REPOSITORY,PR_NUMBER=str(number),BASE_SHA=base,HEAD_SHA=head,
                      REVIEW_COST_POLICY='true',REVIEW_RESULT_CACHE=str(STATE/'cache'))
    return issue_quote(number,base,head) if quote_id is None else request(number,base,head,quote_id)


def issue_quote(number, base, head):
    from preflight import build_quote, restore
    with tempfile.TemporaryDirectory(dir=STATE) as temp, chdir(temp):
        result = Path('review-result')
        result.mkdir()
        evidence = (result/'tests.json').resolve()
        subprocess.run(['node',str(TOOLS/'test_status.cjs'),REPOSITORY,str(number),head,str(evidence)],check=True)
        os.environ['REVIEW_TEST_EVIDENCE'] = str(evidence)
        quote = build_quote(restore())
        plans = manifests()
        admit(plans,quote['binding'])
        current = checked_pr(number)
        if (current['base']['sha'],current['head']['sha']) != (base,head):
            raise ValueError('review revisions changed during admission')
        destination = STATE/quote['id']
        if not destination.exists():
            result.rename(destination)
        return {'quote_id':quote['id'],'base':base,'head':head,'assessment_plans':estimates(plans),
                'phase':quote['phase'],'estimated_phase_usd':quote['estimated_nanodollars']/1e9,
                'next_call_reserved_usd':quote['next_call_reserved_nanodollars']/1e9,'allowance':quote['policy'],
                'blocked_reason':quote['blocked_reason']}


def request(number, base, head, quote_id):
    from qualify import cached_qualification
    if not re.fullmatch('[0-9a-f]{64}',quote_id):
        raise ValueError('invalid quote id')
    saved = STATE/quote_id
    quote = read(saved/'quote.json')
    validate(quote)
    binding = quote['binding']
    _, qualification = cached_qualification()
    if (quote['id'] != quote_id or quote['blocked_reason'] or binding['pr'] != number or
            binding['qualification'] != digest(qualification_identity(qualification)) or
            binding['repository'] != REPOSITORY or binding['base'] != base or binding['head'] != head or
            binding['configuration'] != configuration()):
        raise ValueError('request requires a fresh quote')
    # mkdir claims this quote once, including after process loss.
    attempt = saved/'execution'
    attempt.mkdir()
    with chdir(attempt):
        return execute(saved, quote)


def execute(saved, quote):
    binding = quote['binding']
    base, head = binding['base'], binding['head']
    result = Path('review-result')
    result.mkdir()
    os.environ['REVIEW_TEST_EVIDENCE'] = str(saved/'tests.json')
    for s in STRATEGIES:
        name = s+'-manifest.json'
        (result/name).hardlink_to(saved/name)
    admit(manifests(),binding)
    (result/'preflight.json').write_text(json.dumps({'quote':quote,'blocked_reason':None,'commit_id':head}))
    os.environ.update(BEDROCK_REVIEW_ENABLED='true', SAVIOR_HOST_REVIEW='true')
    check_revision()
    subprocess.run([sys.executable,str(TOOLS/'run_ci.py')],check=True)
    report = read(result/'report.json')
    check_revision()
    return {'report':str((result/'report.json').resolve()),'head':head,'base':base,'complete':report['complete'],
            'completed_chunks':report.get('completed_chunks',0),'total_chunks':report.get('total_chunks'),
            'review_failures':report.get('review_failures',[]),
            'qualified':report['qualified'],'disposition':report['disposition'],'blocked_reason':report.get('blocked_reason')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action',required=True)
    replay_args = sub.add_parser('replay')
    replay_args.add_argument('base')
    replay_args.add_argument('head')
    sub.add_parser('quote').add_argument('pr',type=int)
    requested = sub.add_parser('request')
    requested.add_argument('pr',type=int)
    requested.add_argument('quote_id')
    args = parser.parse_args()
    for key in list(os.environ):
        if key.startswith(('REVIEW_', 'GITHUB_', 'BEDROCK_REVIEW_', 'SAVIOR_HOST_REVIEW', 'GIT_')):
            os.environ.pop(key)
    if args.action == 'replay':
        refs = [output('git','rev-parse','--verify','--end-of-options',ref+'^{commit}') for ref in (args.base,args.head)]
        result = replay(*refs)
    else:
        if args.pr <= 0:
            parser.error('positive PR number required')
        result = command(args.pr, args.quote_id if args.action == 'request' else None)
    print(json.dumps(result,indent=2))
    if result.get('complete') is False or result.get('blocked_reason'):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
