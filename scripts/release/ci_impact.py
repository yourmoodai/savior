"""Classify the complete diff before selecting release qualification jobs."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys


SITE_FILES = {'scripts/site.py', 'scripts/test-site.mjs',
              'docs/assets/social-preview.png', '.github/workflows/site.yml'}


REVIEW_DIRS = ('tools/local_review/', 'tools/adversarial_reviewer/', 'tools/review_routing_research/')
REVIEW_TESTS = {'tests/local-review.spec.mjs', 'tests/review-approval.spec.mjs',
                'tests/review-author-gate.spec.mjs', 'tests/review-pilot.spec.mjs',
                'tests/review-status.spec.mjs'}


def change_scope(paths):
    if paths and all(path.startswith('site/') or path in SITE_FILES for path in paths):
        return 'website-only'
    if paths and all(path.startswith(REVIEW_DIRS) or path in REVIEW_TESTS for path in paths):
        return 'reviewer-only'
    return 'full'


def changed_paths(base, head, event):
    if event == 'workflow_dispatch' and not os.environ.get('VALIDATION_TEST'):
        return None
    if any(not re.fullmatch(r'[0-9a-f]{40}', sha) or sha == '0' * 40
           for sha in (base, head)):
        return None
    revisions = [f'{base}...{head}'] if event in ('pull_request', 'pull_request_target') or bool(os.environ.get('VALIDATION_TEST')) else [base, head]
    try:
        output = subprocess.check_output(
            ['git', 'diff', '--name-only', '--no-renames', '-z', *revisions, '--'])
    except subprocess.CalledProcessError:
        return None
    return [path for path in output.decode('utf-8').split('\0') if path]


def check_gate(needs, context_job, always_required, conditional):
    expected = {context_job, *always_required, *conditional}
    if set(needs) != expected or needs[context_job]['result'] != 'success':
        raise ValueError('ci context failed or dependency set changed')
    required = needs[context_job].get('outputs', {}).get('native_required')
    if required not in ('true', 'false'):
        raise ValueError('missing ci impact decision')
    for name in always_required:
        if needs[name]['result'] != 'success':
            raise ValueError(f'required check did not succeed: {name}')
    for name in conditional:
        expected_result = 'success' if required == 'true' else 'skipped'
        if needs[name]['result'] != expected_result:
            raise ValueError(f'unexpected check result: {name}')


def main():
    if len(sys.argv) == 2:
        needs = json.loads(os.environ['RESULTS'])
        if sys.argv[1] == 'verification':
            check_gate(needs, 'context', ['tooling', 'security'], ['coreml', 'release-tools'])
        elif sys.argv[1] == 'qualification':
            check_gate(needs, 'gate', [], ['build', 'package', 'installed', 'compatibility'])
        else:
            raise ValueError('unknown ci gate')
        return
    paths = changed_paths(os.environ.get('BASE_SHA', ''), os.environ.get('HEAD_SHA', ''),
                          os.environ['GITHUB_EVENT_NAME'])
    scope = change_scope(paths)
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:
        output.write(f'native_required={str(scope == "full").lower()}\n')
    message = 'Full native qualification required.' if scope == 'full' else f'Native qualification skipped for a {scope} change.'
    print(message)
    if summary := os.environ.get('GITHUB_STEP_SUMMARY'):
        with Path(summary).open('a') as output:
            print(message, file=output)


if __name__ == '__main__':
    main()
