"""Run the installed prompt-box harness; unavailable host coverage blocks publication."""
import os
import hashlib
import uuid
from host_fixture import CASES
from archive import validate as validate_archive
from pathlib import Path
import platform
import subprocess
import sys

import zipfile
import tempfile
from runtime_cache import seed

env=dict(os.environ)
env.pop('SAVIOR_HOST_SUITE', None)
env.pop('SAVIOR_REPLACEMENT', None)
env['SAVIOR_DETECTOR'] = 'bert'
env['SAVIOR_MODEL_DIR']=str(Path(env.get('SAVIOR_MODEL_DIR', '.cache/models/mudes-en-base')).resolve())
report_dir = Path(env.get('SAVIOR_HOST_REPORT_DIR', Path('.cache/release/interception') / str(uuid.uuid4())))
env['SAVIOR_HOST_REPORT_DIR'] = str(report_dir.resolve())
if not Path(env['SAVIOR_MODEL_DIR']).exists():
    subprocess.run(['node','scripts/fetch-model.mjs'],check=True,timeout=600)
with tempfile.TemporaryDirectory(prefix='savior installed ') as directory:
    root=Path(directory).resolve()
    candidates = list(Path('.cache/release').glob('savior-*.zip'))
    if len(candidates) != 1:
        raise ValueError('exactly one candidate archive required')
    candidate = candidates[0]
    validate_archive(candidate.parent, candidate.stem.removeprefix('savior-'))
    with candidate.open('rb') as archive:
        env['SAVIOR_CANDIDATE_SHA256'] = hashlib.file_digest(archive, 'sha256').hexdigest()
    with zipfile.ZipFile(candidate) as bundle:
        for name in ('hooks/compatibility-catalog.ts', 'hooks/compatibility.ts'):
            if bundle.read(name) != Path(name).read_bytes():
                raise ValueError('candidate compatibility catalog differs from checkout')
        bundle.extractall(root)
        for entry in bundle.infolist():
            if entry.external_attr >> 16: (root/entry.filename).chmod(entry.external_attr >> 16)
    env['SAVIOR_PLUGIN_ROOT']=str(root)
    cache=root/'cache';cache.mkdir()
    env['XDG_CACHE_HOME']=str(cache);env['LOCALAPPDATA']=str(cache)
    env['SAVIOR_HOST_CACHE']=str(cache)
    target={'Darwin':'darwin-arm64','Linux':'linux-x64','Windows':'win32-x64'}[platform.system()]
    seed(root,Path('.cache/release'),cache,target)
    failed = False
    for strategy in ('fixed_phrase', 'omit'):
        for scenario in CASES:
            print(f'installed interception: {target} {strategy} {scenario}', flush=True)
            result = subprocess.run(['node','scripts/release/host-evidence.mjs',sys.executable],
                           env={**env, 'SAVIOR_REPLACEMENT_STRATEGY': strategy,
                                'SAVIOR_HOST_SCENARIO': scenario},
                           check=False,timeout=260)
            failed = failed or result.returncode != 0
    subprocess.run(['node', 'scripts/release/host-matrix.mjs', str(report_dir), env['SAVIOR_CANDIDATE_SHA256']], check=True)
    if failed:
        raise SystemExit("installed interception failed")
