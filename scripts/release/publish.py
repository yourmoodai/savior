"""Publish a qualified main-branch tag without overwriting any released artifact."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from version_gate import reject_existing_release, validate_tag
from archive import validate

version=validate_tag(Path.cwd(), os.environ.get('GITHUB_REF', ''), os.environ.get('GITHUB_SHA', ''))
tag='v'+version
reject_existing_release(os.environ['GITHUB_REPOSITORY'], version)
if not os.environ.get('SAVIOR_RELEASE_PUBLIC_KEY'):raise SystemExit('release public key required')
subprocess.run(['node','scripts/release/verify-signature.mjs','.cache/release'],check=True)
subprocess.run(['node','scripts/release/verify-release.mjs','.cache/release',version,os.environ['GITHUB_SHA']],check=True)
validate(Path('.cache/release'),version)
envelope=json.loads(Path('.cache/release/release-envelope.json').read_text())
subprocess.run(['python3', 'scripts/release/compatibility-ci.py', 'report'], check=True)
compatibility = Path('.cache/compatibility/compatibility.json')
if json.loads(compatibility.read_text())['candidate_sha256'] != envelope['assets'][f'savior-{version}.zip']:
    raise SystemExit('compatibility candidate mismatch')
for target in ('darwin-arm64','linux-x64','win32-x64'):
    report=json.loads(Path(f'.cache/release/acceptance-{target}.json').read_text())
    if (report.get('target')!=target or report.get('revision')!=os.environ['GITHUB_SHA']
        or report.get('archive_sha256')!=envelope['assets'][f'savior-{version}.zip']
        or report.get('native_acceptance')!='passed'
        or (target=='darwin-arm64' and report.get('offline_first_launch')!='passed')):
        raise SystemExit('native acceptance evidence missing or stale')
assets=[Path('.cache/release')/name for name in [*envelope['assets'],'release-envelope.json','release-envelope.sig']]
for source in (compatibility, Path(str(compatibility)+'.md'), Path('.cache/compatibility/inventory.json')):
    destination = Path('.cache/release') / ('compatibility-inventory.json' if source.name == 'inventory.json' else source.name)
    shutil.copy2(source, destination)
    assets.append(destination)
assets.append(Path(shutil.make_archive('.cache/release/compatibility-evidence', 'zip', '.cache/compatibility/assessments')))
for artifact in json.loads(Path('models/catalog.json').read_text())['models'][0]['artifacts']:
    source=Path('.cache/models/mudes-en-base')/artifact['file']
    destination=Path('.cache/release')/('model-'+artifact['file'])
    shutil.copy2(source,destination);assets.append(destination)
subprocess.run(['node','scripts/release/verify-release.mjs','.cache/release',version,os.environ['GITHUB_SHA'],'--models'],check=True)
immutable=json.loads(subprocess.check_output(['gh','api',f"repos/{os.environ['GITHUB_REPOSITORY']}/immutable-releases"],text=True))
if not immutable.get('enabled'):raise SystemExit('immutable releases must be enabled')
flags=['--latest=false']+(['--prerelease'] if '-' in version.split('+')[0] else [])
subprocess.run(['gh','release','create',tag,'--verify-tag','--title','Savior '+version,'--generate-notes',*flags,*[str(p) for p in assets]],check=True)
print('release published; update marketplace only after downloaded assets pass verification')
