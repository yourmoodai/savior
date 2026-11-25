"""Bind the complete Windows stage to a protected job output before sealing."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

BINARIES = ('runtime/win32-x64/savior.exe', 'runtime/win32-x64/savior-review.exe',
            'native/savior.win32-x64-msvc.node')
NODE = 'runtime/win32-x64/node.exe'
RELEASE_FILES = {*BINARIES, NODE, 'runtime/win32-x64/NODE-LICENSE',
                 'native/index.cjs', 'native/index.d.ts', 'stage.json'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def context():
    return {key: os.environ['GITHUB_'+key.upper()] for key in
            ('repository', 'repository_id', 'workflow_ref', 'run_id', 'run_attempt', 'sha')}


def inventory(root, kind):
    expected = {'fixture.exe'} if kind == 'fixture' else RELEASE_FILES
    if not root.is_dir() or root.is_symlink():
        raise ValueError('invalid_windows_stage_directory')
    files = {}
    for path in root.rglob('*'):
        if path.is_symlink() or (os.name == 'nt' and path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError('linked_windows_stage_entry')
        if path.is_file():
            files[path.relative_to(root).as_posix()] = digest(path)
        elif not path.is_dir():
            raise ValueError('unsupported_windows_stage_entry')
    if set(files) != expected:
        raise ValueError('invalid_windows_stage_inventory')
    return files


def verify(stage, record_path, expected_digest, kind):
    data = record_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_digest:
        raise ValueError('windows_verification_record_changed')
    record = json.loads(data)
    if record['schema'] != 1 or record['kind'] != kind or record['context'] != context():
        raise ValueError('windows_verification_context_changed')
    if record['files'] != inventory(stage, kind):
        raise ValueError('windows_stage_changed')
    signatures = record['signatures']
    expected = {'fixture.exe'} if kind == 'fixture' else {*BINARIES, NODE}
    if signatures.keys() != expected or any(
            not re.fullmatch('[0-9a-f]{64}', item['signer_sha256']) or
            not re.fullmatch('[0-9a-f]{64}', item['timestamp_signer_sha256']) or
            not item['timestamp'] or not item['publisher'] for item in signatures.values()):
        raise ValueError('incomplete_windows_verification_record')
    return record
