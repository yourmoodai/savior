"""Build an unsigned extension candidate without installing or activating it."""
import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--configuration', choices=['debug', 'release'], default='debug')
    parser.add_argument('--test', action='store_true')
    args = parser.parse_args()
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        parser.error('macos_arm64_host_required')
    source = ROOT / 'platforms/macos'
    output = ROOT / '.cache/network/macos' / args.configuration
    extension = output / 'com.yourmood.savior.network.proxy.systemextension'
    executable = extension / 'Contents/MacOS/savior-network-extension'
    executable.parent.mkdir(parents=True, exist_ok=True)
    flags = ['xcrun', 'swiftc', '-swift-version', '5', '-target', 'arm64-apple-macos15.0',
             '-warnings-as-errors', '-module-name', 'SaviorNetworkExtension']
    flags += ['-O'] if args.configuration == 'release' else ['-Onone', '-g']
    subprocess.run(flags + [str(source / name) for name in
                   ['Policy.swift', 'Relay.swift', 'Provider.swift', 'main.swift']]
                   + ['-o', str(executable)], check=True)
    shutil.copy2(source / 'extension-info.plist', extension / 'Contents/Info.plist')
    subprocess.run(['plutil', '-lint', str(extension / 'Contents/Info.plist'),
                    str(source / 'extension-entitlements.plist')], check=True)
    if args.test:
        test = output / 'policy-tests'
        subprocess.run(flags + [str(source / name) for name in ['Policy.swift', 'PolicyTests.swift']]
                       + ['-o', str(test)], check=True)
        subprocess.run([str(test)], check=True)
    print(json.dumps({'version': 1, 'status': 'unsigned_candidate', 'enforced': False,
                      'extension': str(extension), 'activated': False}))


if __name__ == '__main__':
    main()
