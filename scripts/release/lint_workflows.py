"""Run the pinned workflow linter and validate local composite dependencies."""

import hashlib
from pathlib import Path
import platform
import subprocess
import tarfile
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
VERSION = "1.7.12"
ARCHIVES = {
    ("Linux", "x86_64"): ("linux_amd64", "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"),
    ("Darwin", "arm64"): ("darwin_arm64", "aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f"),
}


def main():
    target, expected = ARCHIVES[(platform.system(), platform.machine())]
    directory = ROOT / ".cache" / "actionlint" / VERSION / target
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / "actionlint.tar.gz"
    if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        urllib.request.urlretrieve(
            f"https://github.com/rhysd/actionlint/releases/download/v{VERSION}/actionlint_{VERSION}_{target}.tar.gz",
            archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise ValueError("actionlint checksum mismatch")
    binary = directory / "actionlint"
    with tarfile.open(archive) as bundle, bundle.extractfile("actionlint") as source:
        binary.write_bytes(source.read())
    binary.chmod(0o755)
    workflows = sorted([*(ROOT / ".github/workflows").glob("*.yml"),
                        *(ROOT / ".github/workflows").glob("*.yaml")])
    subprocess.run([str(binary), *map(str, workflows)], cwd=ROOT, check=True)
    subprocess.run(["node", "scripts/release/workflow-policy.cjs"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
