"""Validate the exact plugin archive covered by the signed release envelope."""
import hashlib
import json
from pathlib import PurePosixPath
import stat
import zipfile


def validate(directory, version):
    archive_path = directory/f'savior-{version}.zip'
    checksum = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    marketplace = json.loads((directory/'marketplace.json').read_text())
    if marketplace['plugins'][0]['source'] != {
        'source':'archive', 'url':f'https://github.com/yourmoodai/savior/releases/download/v{version}/{archive_path.name}',
        'sha256':checksum,
    }:
        raise ValueError('marketplace mismatch')
    if (directory/'SHA256SUMS').read_text() != f'{checksum}  {archive_path.name}\n':
        raise ValueError('archive checksum mismatch')
    with zipfile.ZipFile(archive_path) as bundle:
        names = bundle.namelist()
        if (len(names) != len({n.casefold() for n in names}) or sum(i.file_size for i in bundle.infolist()) > 2_147_483_648):  # 2 GiB
            raise ValueError('invalid archive inventory')
        for entry in bundle.infolist():
            name = PurePosixPath(entry.filename)
            mode = stat.S_IFMT(entry.external_attr >> 16)
            if (name.is_absolute() or any(part in ('','..','.') for part in entry.filename.split('/'))
                    or any(c in entry.filename for c in '\\:<>"|?*') or mode not in (0,stat.S_IFREG)):
                raise ValueError('unsafe archive path')
        manifest = json.loads(bundle.read('release.json'))
        if manifest['version'] != version or set(names) != set(manifest['files']) | {'release.json'}:
            raise ValueError('archive inventory mismatch')
        for name, digest in manifest['files'].items():
            with bundle.open(name) as source:
                if hashlib.file_digest(source,'sha256').hexdigest() != digest:
                    raise ValueError('archive digest mismatch')
        for name in ('runtime-manifest.json','runtime-manifest.sig','release-public.pem'):
            if bundle.read(name) != (directory/name).read_bytes():
                raise ValueError('embedded manifest mismatch')
        runtime=json.loads(bundle.read('runtime-manifest.json'))
        for entry in runtime.get('macos_bundle', []):
            data=bundle.read(entry['path'])
            if len(data)!=entry['bytes'] or hashlib.sha256(data).hexdigest()!=entry['sha256']:
                raise ValueError('bundle digest mismatch')
        for name in ('package.json','.claude-plugin/plugin.json'):
            if json.loads(bundle.read(name))['version'] != version:
                raise ValueError('plugin version mismatch')
    return manifest
