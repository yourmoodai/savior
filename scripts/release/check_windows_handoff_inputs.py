"""Check the release addon's file type and the pinned upstream Node on Windows."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile

from package import NODE, TARGETS, digest


def main():
    inspect = ['pwsh', '-NoProfile', '-NonInteractive', '-File',
               str(Path(__file__).with_name('inspect_windows_signature.ps1'))]
    config = json.loads(os.environ['SAVIOR_RELEASE_CONFIG'])
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        addon = work/'fixture.node'
        shutil.copyfile('windows-handoff/stage/fixture.exe', addon)
        subprocess.run([*inspect, addon], check=True)
        name = 'node-v'+NODE+'-win-x64'
        archive = work/(name+'.zip')
        urllib.request.urlretrieve('https://nodejs.org/dist/v'+NODE+'/'+archive.name, archive)
        if digest(archive) != TARGETS['win32-x64'][1]:
            raise ValueError('node checksum mismatch')
        binary = work/'node.exe'
        with zipfile.ZipFile(archive) as bundle, bundle.open(name+'/node.exe') as source, binary.open('wb') as dest:
            shutil.copyfileobj(source, dest)
        subprocess.run([*inspect, binary, config['identities']['node_windows']], check=True)


if __name__ == '__main__':
    main()
