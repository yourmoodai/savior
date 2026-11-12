"""Exercise a published development marketplace in a disposable user profile."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import time
import urllib.request
import zipfile

from development import verify
from version_gate import development_version

ROOT = Path(__file__).resolve().parents[2]


def installed_path(profile, version):
    registry = json.loads((profile/'plugins/installed_plugins.json').read_text())
    entries = registry['plugins'].get('savior@yourmood.ai-dev', [])
    if len(entries) != 1 or entries[0].get('scope') != 'user' or entries[0].get('version') != version:
        raise ValueError('unexpected_installed_plugin')
    path = Path(entries[0]['installPath']).resolve()
    if not path.is_relative_to(profile.resolve()):
        raise ValueError('installed_plugin_outside_profile')
    return path


def main():
    version = os.environ['GITHUB_REF_NAME'].removeprefix('v')
    if not development_version(version):
        raise ValueError('development_version_required')
    release = ROOT/'.cache/release'
    report_dir = ROOT/'.cache/public-install'
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {'version':version,'revision':os.environ['GITHUB_SHA'],
              'platform':platform.system(),'status':'failed','stage':'download','cases':[]}
    try:
        base = f'https://github.com/yourmoodai/savior/releases/download/v{version}/'
        # The workflow's package key hash is the independent trust anchor.
        manifest = json.loads((release/'release-envelope.json').read_text())
        with tempfile.TemporaryDirectory(prefix='savior-public-assets-') as downloaded:
            directory = Path(downloaded)
            for name in [*manifest['assets'],*manifest['models'],'release-envelope.json','release-envelope.sig']:
                urllib.request.urlretrieve(base+name,directory/name)
            verify(directory,version,os.environ['GITHUB_SHA'],os.environ['DEVELOPMENT_KEY_SHA256'])
            archive = directory/f'savior-{version}.zip'
            report['archive_sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
            binary = ROOT/'tools/host/node_modules/@anthropic-ai/claude-code/bin/claude.exe'
            report['client_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
            with tempfile.TemporaryDirectory(prefix='savior-public-profile-') as temporary:
                home = Path(temporary).resolve()
                profile = home/'.claude'
                env = {'PATH':os.environ['PATH'],'HOME':str(home),'CLAUDE_CONFIG_DIR':str(profile),
                       'XDG_CACHE_HOME':str(home/'cache'),'XDG_STATE_HOME':str(home/'state'),
                       'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC':'1','DISABLE_AUTOUPDATER':'1',
                       'DISABLE_TELEMETRY':'1','DISABLE_ERROR_REPORTING':'1'}
                report['stage'] = 'marketplace_install'
                for arguments in [['marketplace','add',base+'marketplace.json'],
                                  ['install','savior@yourmood.ai-dev','--scope','user']]:
                    subprocess.run([str(binary),'plugin',*arguments],cwd=home,env=env,
                                   capture_output=True,check=True,timeout=600)
                plugin = installed_path(profile,version)
                with zipfile.ZipFile(archive) as bundle:
                    for name in bundle.namelist():
                        path = plugin/name
                        if path.is_symlink() or path.read_bytes() != bundle.read(name):
                            raise ValueError('installed_bytes_differ')
                if platform.system() == 'Darwin':
                    report['stage'] = 'quarantine_acceptance'
                    app = plugin/'runtime/darwin-arm64/SaviorRuntime.app'
                    subprocess.run(['xattr','-r','-w','com.apple.quarantine',
                                    f'0083;{int(time.time()):x};Savior development test;',str(app)],check=True)
                    assessment = subprocess.run(['spctl','--assess','--type','execute',str(app)],capture_output=True)
                    report['quarantine_acceptance'] = 'passed' if assessment.returncode == 0 else 'blocked'
                    if assessment.returncode:
                        raise ValueError('macos_publisher_trust_blocked')
                report['stage'] = 'installed_host'
                for strategy in ['fixed_phrase','omit']:
                    for scenario in ['success','unchanged','protected']:
                        case_env = {**env,'SAVIOR_PUBLIC_INSTALL_HOME':str(home),
                                    'SAVIOR_PLUGIN_ROOT':str(plugin),'SAVIOR_CLAUDE_BINARY':str(binary),
                                    'SAVIOR_REPLACEMENT_STRATEGY':strategy,'SAVIOR_HOST_SCENARIO':scenario}
                        result = subprocess.run(['python3','scripts/test-installed-mod.py'],cwd=ROOT,
                                                env=case_env,capture_output=True,text=True,timeout=720)
                        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
                        completed = any(event.get('event')=='completed' for event in events)
                        report['cases'].append({'strategy':strategy,'scenario':scenario,
                                                'passed':result.returncode==0 and completed,'events':events})
                        if result.returncode or not completed:
                            raise ValueError('public_installed_host_failed')
                report['stage'] = 'offline_setup_reuse'
                # Reject any bootstrap fetch; no OS firewall changes are needed.
                script = "const m=await import(process.argv[1]); await m.prepare_runtime(process.argv[2],()=>{throw Error('unexpected_network')});"
                subprocess.run(['node','--input-type=module','-e',script,
                                (plugin/'bridge/bootstrap.mjs').as_uri(),str(plugin)],env=env,check=True,timeout=60)
                report['offline_bootstrap_reuse'] = 'passed'
        report['status'] = 'passed'
        report['stage'] = 'completed'
    except Exception as error:
        report['error_type'] = type(error).__name__
        raise
    finally:
        (report_dir/'result.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__ == '__main__':
    main()
