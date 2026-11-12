"""Fault injection changes only a private copy and never its runtime flags."""
import json
from pathlib import Path
import shutil

HARNESS = Path(__file__).resolve().parents[2]
CASES = json.loads((Path(__file__).with_name('host-cases.json')).read_text())


def prepare(root, home, scenario):
    if CASES[scenario]['suite'] != 'instrumented_failure':
        return root
    plugin = home / 'plugin'
    packaged = (root / 'runtime-manifest.json').exists()
    if scenario == 'startup_failure' and not packaged:
        raise ValueError('startup_requires_packaged_candidate')
    if packaged:
        shutil.copytree(root, plugin)
    else:
        shutil.copytree(root, plugin, ignore=shutil.ignore_patterns('.git', '.cache', 'target', 'node_modules'))
        for name in ('target', 'node_modules'):
            (plugin / name).symlink_to(root / name, target_is_directory=True)
    (plugin / 'bridge/cli.mjs').rename(plugin / 'bridge/cli.real.mjs')
    shutil.copyfile(HARNESS / 'tests/fixtures/host-failure.mjs', plugin / 'bridge/cli.mjs')
    if scenario == 'startup_failure':
        arm(plugin, scenario)
    return plugin


def arm(plugin, scenario):
    (plugin / 'bridge/host-fault.json').write_text(json.dumps({'scenario': scenario}))


def recover(plugin):
    (plugin / 'bridge/host-fault.json').unlink(missing_ok=True)


def worker_alive(plugin):
    import os
    path = plugin / 'bridge/host-worker.pid'
    if not path.exists():
        raise ValueError('stalled_worker_not_observed')
    pid = int(path.read_text())
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            if kernel.GetLastError() == 87:  # ERROR_INVALID_PARAMETER: process no longer exists.
                return False
            raise OSError('worker_state_unavailable')
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise OSError('worker_state_unavailable')
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
