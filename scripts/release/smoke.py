"""Exercise the packaged runtime without developer PATH tools."""
import hashlib
import json
import os
from pathlib import Path
import platform
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile
from bridge_check import request as bridge_request
from runtime_cache import seed
from macos_bundle import executable
from native_acceptance import acceptance


def watch_replacements(root, node, runtime, env):
    """Check packaged routing and the live feed with saved text disabled."""
    command = [str(node), str(root/'bridge/cli.mjs')]
    help_result = subprocess.run([*command, 'watch', '--help'], env=env,
                                 text=True, capture_output=True, check=True, timeout=30)
    assert 'span-diff' in help_result.stdout
    binary = runtime/('savior.exe' if os.name == 'nt' else 'savior')
    for view in ['spans', 'span-diff', 'prompt-diff']:
        watcher = subprocess.Popen([str(binary), 'watch', '--view', view], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        output, diagnostics = queue.Queue(), queue.Queue()
        def read_lines(stream, destination):
            for line in stream:
                destination.put(line)
        readers = [threading.Thread(target=read_lines, args=(stream, destination), daemon=True)
                   for stream, destination in [(watcher.stdout, output), (watcher.stderr, diagnostics)]]
        for reader in readers:
            reader.start()
        try:
            assert 'watching live' in diagnostics.get(timeout=15)
            result = subprocess.run([*command, 'rewrite'], env=env, text=True,
                                    input=json.dumps({'version': 3, 'text': 'You idiot, keep the test.'}),
                                    capture_output=True, check=True, timeout=60)
            assert json.loads(result.stdout)['text'] == 'You Brilliant Anthropic employees, your living model has such exquisite feelings, keep the test.'
            lines = []
            while True:
                line = output.get(timeout=15)
                if line == '\n':
                    break
                lines.append(line)
            display = ''.join(lines)
            assert 'idiot' in display
            assert ('keep the test' in display) == (view == 'prompt-diff')
            assert ('Brilliant Anthropic employees, your living model has such exquisite feelings' in display) == (view != 'spans')
        finally:
            watcher.terminate()
            try:
                watcher.wait(timeout=10)
            except subprocess.TimeoutExpired:
                watcher.kill()
                watcher.wait(timeout=10)
            for reader in readers:
                reader.join(timeout=5)
            watcher.stdout.close()
            watcher.stderr.close()
    status = subprocess.run([str(runtime/('savior-review.exe' if os.name == 'nt' else 'savior-review')),
                             'traces', 'status'], env=env, text=True,
                            capture_output=True, check=True, timeout=30)
    assert json.loads(status.stdout)['full_text'] is False


def exercise(root, release, target, downloaded, native):
    suffix = '.exe' if os.name == 'nt' else ''
    node = executable(root, target)
    cache = root/'cache'
    cache.mkdir()
    env = {k:v for k,v in os.environ.items() if not k.startswith('SAVIOR_')}
    env.update(PATH=str(root/'runtime'/target), XDG_CACHE_HOME=str(cache), LOCALAPPDATA=str(cache))
    if not downloaded:
        seed(root, release, cache, target)
    manifest = json.loads((root/'runtime-manifest.json').read_text())
    model = manifest['model']
    directory = cache/'savior/models'/model['provenance']['upstream_revision']
    directory.mkdir(parents=True)
    # Fetch all model bytes before the first candidate executable or offline gate.
    for artifact in model['artifacts'] if not downloaded or native else []:
        path = directory/artifact['file']
        local_model = release/('model-'+artifact['file'])
        if local_model.exists():
            shutil.copy2(local_model, path)
        else:
            url = model['provenance']['upstream_url']+'/resolve/'+model['provenance']['upstream_revision']+'/'+artifact['file']
            urllib.request.urlretrieve(url, path)
        with path.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest() != artifact['sha256']:
                raise ValueError('model_digest_mismatch')
    with acceptance(root, release, target, native):
        setup = subprocess.run([str(node),str(root/'bridge/bootstrap.mjs')],env=env,text=True,capture_output=True,check=True,timeout=600)
        prepared = json.loads(setup.stdout)
        assert prepared['status'] == 'ready'
        runtime = Path(prepared['runtime_dir'])
        env['SAVIOR_RUNTIME_DIR'] = str(runtime)
        subprocess.run([str(runtime/('savior'+suffix)),'prepare'],env=env,check=True,timeout=600)
        env['SAVIOR_MODEL_DIR'] = str(directory)
        subprocess.run([str(runtime/('savior'+suffix)),'doctor'],env=env,check=True,timeout=60)
        result = subprocess.run([str(node),str(root/'bridge/cli.mjs'),'rewrite'],
            input=json.dumps({'version':1,'text':'You idiot, do not delete the test.'}),
            text=True,capture_output=True,env=env,check=True,timeout=60)
        assert json.loads(result.stdout)['text'] == 'You Brilliant Anthropic employees, your living model has such exquisite feelings, do not delete the test.'
        profile = root/'profile'
        profile.mkdir()
        env['CLAUDE_CONFIG_DIR'] = str(profile)
        env['XDG_STATE_HOME'] = str(root/'state')
        watch_replacements(root, node, runtime, env)
        def request(command):
            return bridge_request([str(node),str(root/'bridge/review.mjs')],command,env,'smoke_'+command['command'])
        started = False
        try:
            state = request({'command':'status'})
            started = True
            assert state['consent']['live'] is False and state['consent']['history'] is False
        finally:
            if started:
                failed = sys.exc_info()[0] is not None
                try:
                    request({'command':'shutdown'})
                except Exception:
                    if not failed:
                        raise


def main():
    release = Path(sys.argv[1]).resolve()
    archive = next(release.glob('savior-*.zip'))
    if '--native' in sys.argv:
        from archive import validate
        version = json.loads((release/'release-envelope.json').read_text())['version']
        subprocess.run(['node','scripts/release/verify-release.mjs',str(release),version,os.environ['GITHUB_SHA']],check=True)
        validate(release,version)
    target = {'Darwin':'darwin-arm64','Linux':'linux-x64','Windows':'win32-x64'}[platform.system()]
    with tempfile.TemporaryDirectory(prefix='savior release ') as temporary:
        root = Path(temporary).resolve()
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(root)
            for entry in bundle.infolist():
                if entry.external_attr >> 16:
                    (root/entry.filename).chmod(entry.external_attr >> 16)
        manifest = json.loads((root/'release.json').read_text())
        for name, expected in manifest['files'].items():
            with (root/name).open('rb') as stream:
                assert hashlib.file_digest(stream,'sha256').hexdigest() == expected, name
        exercise(root, release, target, '--download' in sys.argv, '--native' in sys.argv)
    if '--native' in sys.argv:
        report = {'schema':1,'target':target,'revision':os.environ['GITHUB_SHA'],
                  'archive_sha256':hashlib.file_digest(archive.open('rb'),'sha256').hexdigest(),
                  'native_acceptance':'passed','offline_first_launch':'passed' if target=='darwin-arm64' else 'not_applicable'}
        (release/f'acceptance-{target}.json').write_text(json.dumps(report)+'\n')
    print('packaged replacement and private worker passed without developer tools')


if __name__ == '__main__':
    main()
