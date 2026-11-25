import platform


TARGETS = {('Darwin', 'arm64'): 'darwin-arm64', ('Linux', 'x86_64'): 'linux-x64',
           ('Linux', 'aarch64'): 'linux-arm64', ('Windows', 'AMD64'): 'win32-x64'}


def native_target():
    system, machine = platform.system(), platform.machine()
    target = TARGETS.get((system, machine))
    if target is None:
        raise ValueError(f'unsupported native target: {system} {machine}')
    return target
