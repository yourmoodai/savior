"""Install the checksum-pinned container scanner without extracting archive paths."""

import hashlib
import json
from pathlib import Path
import platform
import tarfile
import tempfile
import urllib.request


ROOT = Path(__file__).resolve().parents[1]


def install():
    system = platform.system()
    supported = {('Linux', 'x86_64'), ('Darwin', 'arm64')}
    if (system, platform.machine()) not in supported:
        raise ValueError('unsupported scanner platform')
    lock = json.loads((ROOT / 'scripts/security-tools.lock.json').read_text())
    spec = lock['assets']['macOS' if system == 'Darwin' else system]
    destination = ROOT / '.cache/security/bin/trivy'
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / 'trivy.tar.gz'
        urllib.request.urlretrieve(spec['url'], archive)
        with archive.open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != spec['sha256']:
                raise ValueError('scanner checksum mismatch')
        with tarfile.open(archive) as bundle:
            member = bundle.getmember('trivy')
            if not member.isfile():
                raise ValueError('invalid scanner binary')
            with bundle.extractfile(member) as source:
                destination.write_bytes(source.read())
    destination.chmod(0o755)
    print(destination.parent)


if __name__ == '__main__':
    install()
