"""Native policy checks; macOS network isolation runs only on disposable CI hosts."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid
from macos_bundle import BUNDLE, LAUNCHER, NODE, RUNTIME
from windows_identity import profile_eku


def run(*args, **kwargs):
    return subprocess.check_output(list(args), timeout=120, **kwargs)


@contextmanager
def offline_macos():
    if os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise ValueError('offline_gate_requires_disposable_github_host')
    status = run('sudo','/sbin/pfctl','-s','info', stderr=subprocess.STDOUT)
    enabled = b'Status: Enabled' in status
    # A hosted runner is disposable. Restore its stock policy even after failure.
    try:
        run('sudo','/sbin/pfctl','-f','-', input=b'set skip on lo0\nblock drop all\n')
        run('sudo','/sbin/pfctl','-F','states')
        if not enabled:
            run('sudo','/sbin/pfctl','-e')
        probe = subprocess.run(['/usr/bin/curl','--connect-timeout','3','--max-time','5',
                                'https://1.1.1.1'], capture_output=True, timeout=10)
        if probe.returncode == 0:
            raise ValueError('offline_network_block_failed')
        yield
    finally:
        run('sudo','/sbin/pfctl','-f','/etc/pf.conf')
        if not enabled:
            run('sudo','/sbin/pfctl','-d')


def apple_identity(path, expected):
    run('/usr/bin/codesign','--verify','--strict',str(path))
    with tempfile.TemporaryDirectory() as directory:
        prefix = str(Path(directory)/'certificate')
        run('/usr/bin/codesign','--display','--extract-certificates',prefix,str(path))
        if hashlib.sha256(Path(prefix+'0').read_bytes()).hexdigest() != expected:
            raise ValueError('native_apple_identity_mismatch')


@contextmanager
def acceptance(root, release, target, enabled):
    if not enabled:
        yield
        return
    config = json.loads(os.environ['SAVIOR_RELEASE_CONFIG'])
    identities = config['identities']
    eku = profile_eku(config)
    pinned = ['apple','node_apple','node_windows']
    if eku is None:
        pinned.append('windows')
    if any(not re.fullmatch(r'[0-9a-f]{64}',identities.get(key,''))
           for key in pinned):
        raise ValueError('native_expected_identities_required')
    if target == 'darwin-arm64':
        bundle = root/BUNDLE
        quarantine = f'0083;{int(time.time()):x};SaviorReleaseQualification;{uuid.uuid4()}'
        run('/usr/bin/xattr','-r','-w','com.apple.quarantine',quarantine,str(bundle))
        with offline_macos():
            # Launch before policy assessment can populate the notarization cache.
            yield
            run('/usr/bin/codesign','--verify','--deep','--strict',str(bundle))
            run('/usr/bin/xcrun','stapler','validate',str(bundle))
            run('/usr/sbin/spctl','--assess','--type','execute',str(bundle))
            for name in ('savior','savior-review','savior.node'):
                apple_identity(root/RUNTIME/name,identities['apple'])
            apple_identity(root/LAUNCHER,identities['apple'])
            apple_identity(root/NODE,identities['node_apple'])
    else:
        if target == 'win32-x64':
            manifest = json.loads((release/'runtime-manifest.json').read_text())
            paths = [(release/entry['asset'],entry['signer_sha256'] if eku else identities['windows'],eku)
                     for entry in manifest['targets'][target]]
            paths.append((root/'runtime/win32-x64/node.exe',identities['node_windows'],None))
            for path, fingerprint, expected_eku in paths:
                if not re.fullmatch(r'[0-9a-f]{64}', fingerprint):
                    raise ValueError('native_expected_identities_required')
                env = {**os.environ,'SAVIOR_NATIVE_FILE':str(path),'SAVIOR_NATIVE_FINGERPRINT':fingerprint,
                       'SAVIOR_NATIVE_PROFILE_EKU':expected_eku or ''}
                run('powershell','-NoProfile','-NonInteractive','-Command',
                    "$s=Get-AuthenticodeSignature -LiteralPath $env:SAVIOR_NATIVE_FILE; "
                    "if ($s.Status -ne 'Valid' -or $null -eq $s.TimeStamperCertificate) { exit 1 }; "
                    "if ($env:SAVIOR_NATIVE_PROFILE_EKU) { "
                    "$eku=$s.SignerCertificate.Extensions['2.5.29.37']; "
                    "if ($null -eq $eku -or $eku.EnhancedKeyUsages.Value -notcontains $env:SAVIOR_NATIVE_PROFILE_EKU) { exit 1 } }; "
                    "$h=[System.Security.Cryptography.SHA256]::Create(); "
                    "$f=([BitConverter]::ToString($h.ComputeHash($s.SignerCertificate.RawData))).Replace('-','').ToLowerInvariant(); "
                    "if ($f -ne $env:SAVIOR_NATIVE_FINGERPRINT) { exit 1 }",env=env)
        yield
