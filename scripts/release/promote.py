"""Promote verified immutable assets with an atomic marketplace branch update."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from archive import validate
from configure_github import gh_api


def qualified_run(repository, run_id, revision, tag, api=gh_api):
    prefix = f'repos/{repository}'
    workflow = api(prefix+'/actions/workflows/release.yml')
    run = api(prefix+f'/actions/runs/{run_id}')
    if (run.get('workflow_id') != workflow['id'] or run.get('head_sha') != revision
            or run.get('head_branch') != tag or run.get('event') != 'push'
            or run.get('status') != 'completed' or run.get('conclusion') != 'success'
            or run.get('repository',{}).get('full_name') != repository):
        raise ValueError('release_run_not_qualified')
    jobs = api(prefix+f"/actions/runs/{run_id}/attempts/{run['run_attempt']}/jobs?per_page=100")
    expected = {'gate','package','publish'} | {
        f'{job} ({os_name})' for job in ('installed','published-download')
        for os_name in ('macos-14','ubuntu-24.04','windows-2022')} | {
        f'build ({os_name}, {target})' for os_name,target in (
            ('macos-14','darwin-arm64'),('ubuntu-24.04','linux-x64'),('windows-2022','win32-x64'))}
    if (jobs['total_count'] != len(jobs['jobs']) or {job['name'] for job in jobs['jobs']} != expected
            or any(job['conclusion'] != 'success' for job in jobs['jobs'])):
        raise ValueError('release_jobs_not_qualified')
    return run['run_attempt']


def advance(repository, version, marketplace, evidence, expected_head, api=gh_api):
    prefix = f'repos/{repository}'
    refs = api(prefix+'/git/matching-refs/heads/marketplace')
    ref = next((item for item in refs if item['ref']=='refs/heads/marketplace'),None)
    head = ref['object']['sha'] if ref else None
    if head != expected_head:
        raise ValueError('marketplace_changed_since_approval')
    additions = [{'path':'marketplace.json','contents':base64.b64encode(marketplace).decode()},
                 {'path':'promotion.json','contents':base64.b64encode((json.dumps(evidence,indent=2)+'\n').encode()).decode()}]
    if head:
        query = '''mutation($input:CreateCommitOnBranchInput!){createCommitOnBranch(input:$input){commit{oid}}}'''
        result = api('graphql',method='POST',payload={'query':query,'variables':{'input':{
            'branch':{'repositoryNameWithOwner':repository,'branchName':'marketplace'},
            'expectedHeadOid':head,'message':{'headline':'chore: promote v'+version},
            'fileChanges':{'additions':additions}}}})
        if result.get('errors'):
            raise ValueError('marketplace_update_rejected')
    else:
        tree = api(prefix+'/git/trees',method='POST',payload={'tree':[
            {'path':item['path'],'mode':'100644','type':'blob','content':base64.b64decode(item['contents']).decode()}
            for item in additions]})
        commit = api(prefix+'/git/commits',method='POST',payload={
            'message':'chore: promote v'+version,'tree':tree['sha'],'parents':[]})
        # Creation fails if another promotion won the race. Never overwrite it.
        api(prefix+'/git/refs',method='POST',payload={'ref':'refs/heads/marketplace','sha':commit['sha']})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('version')
    parser.add_argument('run_id',type=int)
    parser.add_argument('--expected-head',required=True,help='marketplace SHA reviewed, or absent for first promotion')
    args = parser.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+',args.version):
        raise ValueError('stable_release_version_required')
    if args.expected_head != 'absent' and not re.fullmatch(r'[a-f0-9]{40}',args.expected_head):
        raise ValueError('invalid_marketplace_head')
    if os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise ValueError('promotion_requires_main')
    repository = os.environ['GITHUB_REPOSITORY']
    tag = 'v'+args.version
    revision = subprocess.check_output(['git','rev-parse',f'refs/tags/{tag}^{{commit}}'],text=True).strip()
    subprocess.run(['git','merge-base','--is-ancestor',revision,'origin/main'],check=True)
    attempt = qualified_run(repository,args.run_id,revision,tag)
    release = gh_api(f'repos/{repository}/releases/tags/{tag}')
    if release.get('draft') or release.get('prerelease') or not release.get('immutable'):
        raise ValueError('immutable_stable_release_required')
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        subprocess.run(['gh','release','download',tag,'--repo',repository,'--dir',temporary],check=True)
        subprocess.run(['node','scripts/release/verify-release.mjs',temporary,args.version,revision,'--models'],check=True)
        validate(directory,args.version)
        envelope = json.loads((directory/'release-envelope.json').read_text())
        # Recheck the current attempt after downloads; rerunning failed jobs revokes evidence.
        if qualified_run(repository,args.run_id,revision,tag) != attempt:
            raise ValueError('release_attempt_changed')
        evidence = {'schema':1,'version':args.version,'revision':revision,'release_run':args.run_id,
                    'run_attempt':attempt,'assets':envelope['assets'],'models':envelope['models']}
        advance(repository,args.version,(directory/'marketplace.json').read_bytes(),evidence,
                None if args.expected_head=='absent' else args.expected_head)


if __name__ == '__main__':
    main()
