"""Install hash-pinned release tooling into a container build, without credentials."""
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request


def install():
    tools = json.loads(Path(__file__).with_name('tools.lock.json').read_text())['tools']
    architecture = platform.machine()
    if architecture not in ('x86_64', 'aarch64'):
        raise SystemExit('unsupported_tooling_architecture')
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        def fetch(name):
            spec = tools[name]
            path = work/name
            urllib.request.urlretrieve(spec['url'], path)
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != spec['sha256']:
                    raise SystemExit('tool_digest_mismatch')
            return path
        with tarfile.open(fetch('rcodesign_'+architecture)) as archive:
            member = next(m for m in archive.getmembers() if Path(m.name).name == 'rcodesign' and m.isfile())
            with archive.extractfile(member) as source, Path('/usr/local/bin/rcodesign').open('wb') as output:
                shutil.copyfileobj(source, output)
        Path('/usr/local/bin/rcodesign').chmod(0o755)
        Path('/opt/jsign').mkdir()
        shutil.copyfile(fetch('jsign'), '/opt/jsign/jsign.jar')
        launcher = Path('/usr/local/bin/jsign')
        launcher.write_text('#!/bin/sh\nexec java -jar /opt/jsign/jsign.jar "$@"\n')
        launcher.chmod(0o755)
        node = work/'node'
        node.mkdir()
        with tarfile.open(fetch('node_'+architecture)) as archive:
            archive.extractall(node, filter='data')
        subprocess.run(['cp', '-a', str(next(node.iterdir()))+'/.', '/usr/local/'], check=True)
        source = work/'osslsigncode-source'
        source.mkdir()
        with tarfile.open(fetch('osslsigncode')) as archive:
            archive.extractall(source, filter='data')
        subprocess.run(['cmake', '-S', str(next(source.iterdir())), '-B', str(work/'build'),
                        '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Release', '-DBUILD_TESTING=OFF'], check=True)
        subprocess.run(['cmake', '--build', str(work/'build'), '--parallel', '2'], check=True)
        subprocess.run(['cmake', '--install', str(work/'build')], check=True)


if __name__ == '__main__':
    install()
