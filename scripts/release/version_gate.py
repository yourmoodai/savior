"""Validate one release version across the plugin, npm, and Rust workspace."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib


SEMVER = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)


def parse_version(value):
    match = SEMVER.fullmatch(value)
    if match is None:
        raise ValueError("invalid semver version")
    prerelease = match[4]
    identifiers = []
    for part in prerelease.split(".") if prerelease else []:
        if part.isascii() and part.isdecimal():
            if len(part) > 1 and part.startswith("0"):
                raise ValueError("invalid numeric prerelease identifier")
            identifiers.append((0, int(part)))
        else:
            identifiers.append((1, part))
    return tuple(int(match[i]) for i in (1, 2, 3)), prerelease is None, tuple(identifiers)


def release_version(root):
    package = json.loads((root / "package.json").read_text())
    version = package["version"]
    parse_version(version)
    lock = json.loads((root / "package-lock.json").read_text())
    cargo = tomllib.loads((root / "Cargo.toml").read_text())
    plugin = json.loads((root / ".claude-plugin/plugin.json").read_text())
    versions = [lock["version"], lock["packages"][""]["version"],
                cargo["workspace"]["package"]["version"], plugin["version"]]
    lock_packages = tomllib.loads((root / "Cargo.lock").read_text())["package"]
    for member in cargo["workspace"]["members"]:
        manifest = tomllib.loads((root / member / "Cargo.toml").read_text())["package"]
        if manifest.get("version") != {"workspace": True}:
            continue
        matches = [p["version"] for p in lock_packages
                   if p["name"] == manifest["name"] and "source" not in p]
        if len(matches) != 1:
            raise ValueError("workspace package missing or ambiguous in cargo lockfile")
        versions.extend(matches)
    if any(value != version for value in versions):
        raise ValueError("release versions disagree")
    return version


def validate_tag(root, ref, sha):
    version = release_version(root)
    if ref != f"refs/tags/v{version}":
        raise ValueError("release tag/version mismatch")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("invalid release revision")
    tag_sha = subprocess.check_output(
        ["git", "rev-parse", f"{ref}^{{commit}}"], cwd=root, text=True).strip()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if tag_sha != sha or head != sha:
        raise ValueError("release tag/revision mismatch")
    subprocess.run(["git", "merge-base", "--is-ancestor", sha, "origin/main"],
                   cwd=root, check=True)
    return version


def reject_existing_release(repository, version):
    pages = json.loads(subprocess.check_output(
        ["gh", "api", f"repos/{repository}/releases", "--paginate", "--slurp"], text=True))
    if any(release["tag_name"] == f"v{version}" for page in pages for release in page):
        raise ValueError("release already exists; published assets are immutable")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", action="store_true")
    args = parser.parse_args()
    root = Path.cwd()
    version = (validate_tag(root, os.environ.get("GITHUB_REF", ""),
                            os.environ.get("GITHUB_SHA", ""))
               if args.tag else release_version(root))
    if args.tag:
        reject_existing_release(os.environ["GITHUB_REPOSITORY"], version)
    print(f"version={version}")


if __name__ == "__main__":
    main()
