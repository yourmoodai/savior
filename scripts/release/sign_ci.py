"""Materialize environment credentials privately and build a signed candidate."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    # Environment names are fixed; configuration cannot choose arbitrary secrets.
    names = ('apple_p12', 'apple_password', 'apple_api', 'windows_keystore',
             'windows_password', 'windows_ca', 'timestamp_ca', 'manifest_private', 'manifest_public')
    config = json.loads(os.environ['SAVIOR_RELEASE_CONFIG'])
    if config.get('tools') != {name: name for name in ('rcodesign','jsign','osslsigncode','openssl','node','npm')}:
        raise ValueError('unexpected_release_tools')
    root = Path.cwd().resolve()
    with tempfile.TemporaryDirectory(prefix='savior-signing-') as temporary:
        private = Path(temporary)
        private.chmod(0o700)
        config['credentials'] = {name: 'SAVIOR_'+name.upper()+'_FILE' for name in names}
        environment = []
        for name in names:
            value = os.environ.get('SAVIOR_'+name.upper()+'_BASE64', '')
            if not value and name == 'windows_keystore' and config['windows']['storetype'] == 'TRUSTEDSIGNING':
                continue
            data = base64.b64decode(value, validate=True)
            if not data:
                raise ValueError('missing_signing_credential')
            path = private/name
            path.write_bytes(data)
            path.chmod(0o600)
            environment += ['--env', config['credentials'][name]+'=/secrets/'+name]
        (private/'config.json').write_text(json.dumps(config))
        (private/'config.json').chmod(0o600)
        subprocess.run(['docker','run','--rm','--user',f'{os.getuid()}:{os.getgid()}',
                        '--volume',str(root)+':/workspace', '--volume',str(private)+':/secrets:ro',
                        '--env','GIT_CONFIG_COUNT=1','--env','GIT_CONFIG_KEY_0=safe.directory',
                        '--env','GIT_CONFIG_VALUE_0=/workspace', *environment,
                        '--entrypoint','python3','savior-release-tools',
                        'scripts/release/local.py','candidate','--config','/secrets/config.json',
                        '--parts','/workspace/.cache/parts','--output','/workspace/.cache/signed-release'], check=True)
        shutil.copytree(root/'.cache/signed-release/candidate', root/'.cache/release')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('signed_candidate_failed; inspect private stage state') from None
