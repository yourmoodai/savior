"""Skip native qualification only when every changed path is website-only."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys


SITE_FILES = {'scripts/site.py', 'scripts/test-site.mjs',
              'docs/assets/social-preview.png', '.github/workflows/site.yml'}


def native_required(paths):
    # Unknown paths include shared dependencies, packaging, and CI itself.
    return any(not (path.startswith('site/') or path in SITE_FILES) for path in paths)


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
            check_gate(needs, 'context', ['tooling'], ['coreml', 'release-tools'])
        elif sys.argv[1] == 'qualification':
            check_gate(needs, 'gate', [], ['build', 'package', 'installed', 'compatibility'])
        else:
            raise ValueError('unknown ci gate')
        return
    paths = changed_paths(os.environ.get('BASE_SHA', ''), os.environ.get('HEAD_SHA', ''),
                          os.environ['GITHUB_EVENT_NAME'])
    required = paths is None or native_required(paths)
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:
        output.write(f'native_required={str(required).lower()}\n')
    print('native build required' if required else 'website-only change; native jobs skipped')


if __name__ == '__main__':
    main()
