"""Coordinate frozen discovery and read-only CI artifacts."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    if sys.argv[1:] == ['plan']:
        candidate_run = os.environ['GITHUB_RUN_ID']
        if os.environ['GITHUB_EVENT_NAME'] in ('schedule', 'workflow_dispatch') and os.environ.get('COMPATIBILITY_STANDALONE') == 'true':
            repository = os.environ['GITHUB_REPOSITORY']
            branch = os.environ['DEFAULT_BRANCH']
            runs = json.loads(subprocess.check_output(['gh', 'api', '--method', 'GET',
                f'repos/{repository}/actions/workflows/qualification.yml/runs',
                '-f', f'branch={branch}', '-f', 'status=success', '-f', 'per_page=20'], text=True))
            matching = [run for run in runs['workflow_runs'] if run['head_sha'] == os.environ['GITHUB_SHA']
                        and run['event'] != 'pull_request' and run['head_repository']['full_name'] == repository]
            if not matching:
                raise SystemExit('no qualified candidate at this revision; run release qualification first')
            candidate_run = str(matching[0]['id'])
        subprocess.run(['node', 'scripts/release/compatibility-inventory.mjs', 'discover',
                        '.cache/compatibility/inventory.json'], check=True)
        matrix = subprocess.check_output(['node', 'scripts/release/compatibility-inventory.mjs', 'matrix',
                                          '.cache/compatibility/inventory.json'], text=True).strip()
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write(f'matrix={matrix}\ncandidate_run={candidate_run}\n')
    elif sys.argv[1:] == ['report']:
        candidates = list(Path('.cache/release').glob('savior-*.zip'))
        if len(candidates) != 1:
            raise SystemExit('exactly one candidate archive required')
        with candidates[0].open('rb') as source:
            candidate = hashlib.file_digest(source, 'sha256').hexdigest()
        result = subprocess.run(['node', 'scripts/release/compatibility-report.mjs',
            '.cache/compatibility/inventory.json', '.cache/compatibility/assessments', candidate,
            '.cache/compatibility/compatibility.json'], check=False)
        summary = Path('.cache/compatibility/compatibility.json.md')
        if summary.exists() and os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
                output.write(summary.read_text())
        raise SystemExit(result.returncode)
    else:
        raise SystemExit('usage: compatibility-ci.py plan|report')


if __name__ == '__main__':
    main()
