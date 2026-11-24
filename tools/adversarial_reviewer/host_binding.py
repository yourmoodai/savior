"""Check the current GitHub and AWS authorities for a maintainer-requested review."""
import json
import os
from pathlib import Path
import re
import subprocess

REPOSITORY = 'yourmoodai/savior'
ACCOUNT = '463509296941'
ROLE = 'savior-bedrock-host'
ROOT = Path(__file__).resolve().parents[2]


def output(*args):
    return subprocess.check_output(args, text=True, timeout=60).strip()


def api(path):
    return json.loads(output('gh', 'api', '--hostname', 'github.com', path))


def checked_pr(number, *, live=False):
    repo = 'repos/'+REPOSITORY
    user = api('user')
    access = api(repo+'/collaborators/'+user['login']+'/permission')
    if user['type'] != 'User' or (access['permission'] != 'admin' and access.get('role_name') != 'maintain'):
        raise ValueError('maintainer authorization required')
    pr = api(repo+'/pulls/'+str(number))
    if (pr['state'] != 'open' or pr['base']['ref'] != 'main' or
        any((pr[s].get('repo') or {}).get('full_name') != REPOSITORY for s in ('base', 'head')) or
        not all(re.fullmatch('[a-f0-9]{40}', pr[s]['sha']) for s in ('base','head'))):
        raise ValueError('current same-repository PR targeting main required')
    if live and pr['draft']:
        raise ValueError('ready PR required for live review')
    if live:
        author = pr['user']['login']
        level = api(repo+'/collaborators/'+author+'/permission')['permission']
        owners = {owner.lower() for line in (ROOT/'.github/CODEOWNERS').read_text().splitlines()
                  for owner in line.split('#')[0].split()[1:]}
        if level != 'admin' and (level not in ('write','maintain') or '@'+author.lower() not in owners):
            raise ValueError('PR author is outside the trusted reviewer scope')
    return pr


def check_deployment(base):
    clean = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
    def git(*args):
        return subprocess.check_output(['git','-C',str(ROOT),*args], env=clean,text=True).strip()
    if git('rev-parse','HEAD') != base or git('status','--porcelain','--','tools/adversarial_reviewer','.github'):
        raise ValueError('install the clean reviewer from the exact trusted base')
    if not os.environ.get('AWS_PROFILE') or os.environ.get('AWS_REGION') != 'us-east-1':
        raise ValueError('scoped AWS profile and us-east-1 required')
    from botocore.session import get_session
    arn = get_session().create_client('sts',region_name='us-east-1').get_caller_identity()['Arn']
    if not arn.startswith('arn:aws:sts::'+ACCOUNT+':assumed-role/'+ROLE+'/'):
        raise ValueError('scoped host review role required')
    deployed = api('repos/'+REPOSITORY+'/actions/variables/SAVIOR_BEDROCK_LEDGER_BUCKET')['value']
    if not deployed or deployed != os.environ.get('SAVIOR_BEDROCK_LEDGER_BUCKET', deployed):
        raise ValueError('shared ledger deployment mismatch')
    os.environ['SAVIOR_BEDROCK_LEDGER_BUCKET'] = deployed
    if api('repos/'+REPOSITORY+'/actions/variables/BEDROCK_REVIEW_ENABLED')['value'] != 'false':
        raise ValueError('drain the legacy reviewer before shared-ledger cutover')


def check_revision():
    pr = checked_pr(int(os.environ['PR_NUMBER']), live=True)
    if (pr['base']['sha'],pr['head']['sha']) != (os.environ['BASE_SHA'],os.environ['HEAD_SHA']):
        raise ValueError('review revisions changed')
