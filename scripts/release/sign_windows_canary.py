"""Sign the reviewed Savior CLI using the release Jsign binary and Azure profile."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.request

from sign_ci import credential
from windows_identity import profile_eku
from windows_signing import signing_arguments


def sign(paths):
    config = json.loads(os.environ['SAVIOR_RELEASE_CONFIG'])
    windows = config['windows']
    if (not profile_eku(config) or windows['authentication'] != 'github-oidc'
            or windows['endpoint'] != 'eus.codesigning.azure.net'
            or windows['alias'] != 'yourmood-signing/yourmood-public'
            or windows['timestamp_url'] != 'http://timestamp.acs.microsoft.com'):
        raise ValueError('unexpected_release_signing_profile')
    spec = json.loads(Path(__file__).with_name('tools.lock.json').read_text())['tools']['jsign']
    with tempfile.TemporaryDirectory() as directory:
        jar = Path(directory)/'jsign.jar'
        urllib.request.urlretrieve(spec['url'], jar)
        with jar.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != spec['sha256']:
                raise ValueError('tool_digest_mismatch')
        token = Path(directory)/'signing-token'
        token.write_bytes(credential('windows_password', windows))
        args = signing_arguments(windows, windows['endpoint'], token, paths)
        subprocess.run(['java', '-jar', jar, *args], check=True, capture_output=True, timeout=600)


def main():
    sign([Path('target/release/savior.exe').resolve(strict=True)])


if __name__ == '__main__':
    main()
