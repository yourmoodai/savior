"""Publish integrity-checked development artifacts without platform signing."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from archive import validate
from version_gate import development_version, reject_existing_release, validate_tag


def gate():
    version = validate_tag(Path.cwd(), os.environ['GITHUB_REF'], os.environ['GITHUB_SHA'])
    if not development_version(version):
        raise ValueError('development_version_required')
    reject_existing_release(os.environ['GITHUB_REPOSITORY'], version)
    return version


def verify(directory, version, revision, expected_key_hash):
    key = (directory/'release-public.pem').read_bytes()
    if hashlib.sha256(key).hexdigest() != expected_key_hash:
        raise ValueError('development_key_mismatch')
    subprocess.run(['node', 'scripts/release/verify-release.mjs', str(directory), version, revision,
                    '--models'], env={**os.environ, 'SAVIOR_RELEASE_PUBLIC_KEY': key.decode()}, check=True)
    validate(directory, version)
    if json.loads((directory/'marketplace.json').read_text())['name'] != 'yourmood.ai-dev':
        raise ValueError('development_marketplace_required')


def publish(directory):
    version = gate()
    verify(directory, version, os.environ['GITHUB_SHA'], os.environ['DEVELOPMENT_KEY_SHA256'])
    repository = os.environ['GITHUB_REPOSITORY']
    immutable = json.loads(subprocess.check_output(['gh','api',f'repos/{repository}/immutable-releases'],text=True))
    if not immutable.get('enabled'):
        raise ValueError('immutable_releases_required')
    envelope = json.loads((directory/'release-envelope.json').read_text())
    assets = [*envelope['assets'], *envelope['models'], 'release-envelope.json', 'release-envelope.sig']
    notes = directory/'development-notes.md'
    notes.write_text(f'''Development build {version}. No Apple publisher signing/notarization or Windows publisher signing.
Artifact signatures and checksums are retained. Windows testing is deferred.
Public installation checks run after publication; inspect the development release workflow results before relying on this build.

```sh
claude plugin marketplace add https://github.com/yourmoodai/savior/releases/download/v{version}/marketplace.json
claude plugin install savior@yourmood.ai-dev --scope user
```

This feed is separate from the stable marketplace. macOS may reject this build under normal OS protections.
''')
    subprocess.run(['gh','release','create','v'+version,'--verify-tag','--prerelease','--latest=false',
                    '--title','Savior '+version+' (development, no publisher signing)',
                    '--notes-file',str(notes),*[str(directory/name) for name in assets]],check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['gate','models','verify','publish'])
    args = parser.parse_args()
    directory = Path('.cache/release')
    if args.command == 'gate':
        print(gate())
    elif args.command == 'models':
        model = json.loads(Path('models/catalog.json').read_text())['models'][0]
        for artifact in model['artifacts']:
            source = Path('.cache/models/mudes-en-base')/artifact['file']
            if hashlib.sha256(source.read_bytes()).hexdigest() != artifact['sha256']:
                raise ValueError('model_digest_mismatch')
            shutil.copy2(source, directory/('model-'+artifact['file']))
        key_hash = hashlib.sha256((directory/'release-public.pem').read_bytes()).hexdigest()
        with open(os.environ['GITHUB_OUTPUT'],'a') as output:
            output.write('key_sha256='+key_hash+'\n')
    elif args.command == 'verify':
        version = json.loads((directory/'release-envelope.json').read_text())['version']
        verify(directory,version,os.environ['GITHUB_SHA'],os.environ['DEVELOPMENT_KEY_SHA256'])
    else:
        publish(directory)


if __name__ == '__main__':
    main()
