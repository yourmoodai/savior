"""Publish a qualified main-branch tag without overwriting any released artifact."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from version_gate import reject_existing_release, validate_tag

version=validate_tag(Path.cwd(), os.environ.get('GITHUB_REF', ''), os.environ.get('GITHUB_SHA', ''))
tag='v'+version
reject_existing_release(os.environ['GITHUB_REPOSITORY'], version)
if not os.environ.get('SAVIOR_RELEASE_PUBLIC_KEY'):raise SystemExit('release public key required')
subprocess.run(['node','scripts/release/verify-signature.mjs','.cache/release'],check=True)
assets=[p for p in Path('.cache/release').glob('*') if p.is_file()]
for artifact in json.loads(Path('models/catalog.json').read_text())['models'][0]['artifacts']:
    source=Path('.cache/models/mudes-en-base')/artifact['file']
    destination=Path('.cache/release')/('model-'+artifact['file'])
    shutil.copy2(source,destination);assets.append(destination)
subprocess.run(['gh','release','create',tag,'--verify-tag','--title','Savior '+version,'--generate-notes',*[str(p) for p in assets]],check=True)
print('release published; update marketplace only after downloaded assets pass verification')
