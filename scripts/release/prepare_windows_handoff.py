"""Validate the input stage before signing and describe the verified output."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from sign_windows_canary import sign
from windows_handoff import BINARIES, NODE, context, digest, inventory, verify


def prepare(directory, kind):
    stage = directory/'stage'
    before = inventory(stage, kind)
    targets = ('fixture.exe',) if kind == 'fixture' else BINARIES
    record = {'schema': 1, 'kind': kind, 'context': context(), 'signatures': {}}
    if kind == 'release':
        metadata_path = stage/'stage.json'
        metadata = json.loads(metadata_path.read_bytes())
        if (metadata['schema'] != 1 or metadata['target'] != 'win32-x64' or
                metadata['revision'] != os.environ['GITHUB_SHA'] or
                metadata['version'] != json.loads(Path('package.json').read_bytes())['version'] or
                metadata['files'] != {p: h for p, h in before.items() if p != 'stage.json'}):
            raise ValueError('invalid_unsigned_windows_stage')
        node_pin = json.loads(os.environ['SAVIOR_RELEASE_CONFIG'])['identities']['node_windows']
        if not re.fullmatch('[0-9a-f]{64}', node_pin):
            raise ValueError('missing_upstream_node_identity')
        record['unsigned_stage_sha256'] = before['stage.json']

    def inspect(name, *extra):
        result = subprocess.check_output(['pwsh', '-NoProfile', '-NonInteractive', '-File',
            str(Path(__file__).with_name('inspect_windows_signature.ps1')), str(stage/name), *extra])
        record['signatures'][name] = json.loads(result.decode('utf-8-sig'))

    if kind == 'release':
        inspect(NODE, node_pin)
    sign([stage/name for name in targets])
    for name in targets:
        inspect(name)
    after = inventory(stage, kind)
    if any(after[p] != before[p] for p in before if p not in targets):
        raise ValueError('unexpected_windows_signing_change')
    if kind == 'release':
        metadata['files'] = {p: h for p, h in after.items() if p != 'stage.json'}
        metadata_path.write_text(json.dumps(metadata, sort_keys=True)+'\n')
        after['stage.json'] = digest(metadata_path)
    record['files'] = after
    receipt = directory/'verification.json'
    receipt.write_text(json.dumps(record, sort_keys=True, indent=2)+'\n')
    fingerprint = digest(receipt)
    verify(stage, receipt, fingerprint, kind)
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
        print('record_sha256='+fingerprint, file=output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('release', 'fixture'))
    prepare(Path('windows-handoff'), parser.parse_args().kind)
