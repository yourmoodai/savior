import platform


TARGETS = {('Darwin', 'arm64'): 'darwin-arm64', ('Linux', 'x86_64'): 'linux-x64',
           ('Linux', 'aarch64'): 'linux-arm64', ('Windows', 'AMD64'): 'win32-x64'}


def native_target():
    return TARGETS[(platform.system(), platform.machine())]
