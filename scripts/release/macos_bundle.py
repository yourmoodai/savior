"""Assemble the headless bundle without changing upstream Node signatures."""
import plistlib
import shutil

BUNDLE = 'runtime/darwin-arm64/SaviorRuntime.app'
RUNTIME = BUNDLE + '/Contents/Helpers'
LAUNCHER = BUNDLE + '/Contents/MacOS/savior-bootstrap'
NODE = BUNDLE + '/Contents/Frameworks/node'


def create(part, version):
    bundle = part / BUNDLE
    if bundle.exists():
        shutil.rmtree(bundle)
    for directory in ('MacOS', 'Helpers', 'Frameworks', 'Resources'):
        (bundle / 'Contents' / directory).mkdir(parents=True, exist_ok=True)
    for source, destination in [
        ('runtime/darwin-arm64/node', NODE),
        ('runtime/darwin-arm64/savior-bootstrap', LAUNCHER),
        ('runtime/darwin-arm64/savior', RUNTIME+'/savior'),
        ('runtime/darwin-arm64/savior-review', RUNTIME+'/savior-review'),
        ('native/savior.darwin-arm64.node', RUNTIME+'/savior.node'),
    ]:
        shutil.copy2(part/source, part/destination)
        (part/destination).chmod(0o644 if destination.endswith('.node') else 0o755)
    (bundle/'Contents/Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': 'ai.yourmood.savior.runtime',
        'CFBundleName': 'Savior Runtime', 'CFBundleExecutable': 'savior-bootstrap',
        'CFBundlePackageType': 'APPL', 'CFBundleVersion': version.split('-')[0].split('+')[0],
        'CFBundleShortVersionString': version.split('-')[0].split('+')[0], 'LSUIElement': True,
        'LSMinimumSystemVersion': '14.0',
    }))
    return bundle


def executable(plugin, target):
    if target == 'darwin-arm64' and (plugin/BUNDLE).is_dir():
        return plugin/LAUNCHER
    return plugin/'runtime'/target/('node.exe' if target=='win32-x64' else 'node')
