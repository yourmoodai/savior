"""Open an idempotent version-bump PR without creating a release tag."""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib

from version_gate import parse_version, release_version


def version_changes(root, candidate):
    current = release_version(root)
    if parse_version(candidate) <= parse_version(current):
        raise ValueError("release version must increase")
    if len(f"chore: prepare release {candidate}") > 50:
        raise ValueError("release version exceeds commit subject limit")
    changes = {}
    for name in ("package.json", "package-lock.json", ".claude-plugin/plugin.json"):
        value = json.loads((root / name).read_text())
        value["version"] = candidate
        if name == "package-lock.json":
            value["packages"][""]["version"] = candidate
        changes[name] = json.dumps(value, indent=2) + "\n"
    manifest = (root / "Cargo.toml").read_text()
    changes["Cargo.toml"], count = re.subn(
        r'(\[workspace\.package\]\s*\nversion = ")[^"]+(")',
        lambda match: match[1] + candidate + match[2], manifest, count=1)
    if count != 1:
        raise ValueError("expected one workspace version")
    cargo = tomllib.loads(manifest)
    names = []
    for member in cargo["workspace"]["members"]:
        package = tomllib.loads((root / member / "Cargo.toml").read_text())["package"]
        if package.get("version") == {"workspace": True}:
            names.append(package["name"])
    lock = (root / "Cargo.lock").read_text()
    for name in names:
        pattern = r'(\[\[package\]\]\nname = "' + re.escape(name) + r'"\nversion = ")' + re.escape(current) + r'("\n)'
        lock, count = re.subn(pattern, lambda match: match[1] + candidate + match[2], lock)
        if count != 1:
            raise ValueError("expected one workspace lockfile version")
    changes["Cargo.lock"] = lock
    return changes


def gh(*args, payload=None):
    command = ["gh", *args]
    if payload is not None:
        command.extend(["--input", "-"])
    result = subprocess.run(command, input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def prepare(root, candidate, repository, api=gh):
    changes = version_changes(root, candidate)
    branch = f"release/v{candidate}"
    tag = f"v{candidate}"
    tags = api("api", f"repos/{repository}/tags", "--paginate", "--slurp")
    releases = api("api", f"repos/{repository}/releases", "--paginate", "--slurp")
    if any(t["name"] == tag for page in tags for t in page):
        raise ValueError("release tag already exists")
    if any(r["tag_name"] == tag for page in releases for r in page):
        raise ValueError("release already exists")
    prs = api("api", "--method", "GET", f"repos/{repository}/pulls",
              "-f", "state=open", "-f", "base=main", "-f", f"head={repository.split('/')[0]}:{branch}")
    if len(prs) > 1:
        raise ValueError("ambiguous existing release pull request")
    if prs:
        pr = prs[0]
    else:
        branches = api("api", f"repos/{repository}/git/matching-refs/heads/{branch}")
        if any(ref["ref"] == f"refs/heads/{branch}" for ref in branches):
            raise ValueError("release branch exists without an open pull request")
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        api("api", "--method", "POST", f"repos/{repository}/git/refs",
            payload={"ref": f"refs/heads/{branch}", "sha": sha})
        api("api", "graphql", payload={
            "query": "mutation($input: CreateCommitOnBranchInput!) { createCommitOnBranch(input: $input) { commit { oid } } }",
            "variables": {"input": {
                "branch": {"repositoryNameWithOwner": repository, "branchName": branch},
                "expectedHeadOid": sha,
                "message": {"headline": f"chore: prepare release {candidate}"},
                "fileChanges": {"additions": [{"path": path, "contents": base64.b64encode(content.encode()).decode()}
                                               for path, content in changes.items()]},
            }}})
        labels = api("api", f"repos/{repository}/labels", "--paginate", "--slurp")
        if not any(label["name"] == "release" for page in labels for label in page):
            api("api", "--method", "POST", f"repos/{repository}/labels",
                payload={"name": "release", "color": "0e8a16", "description": "prepares a tagged release"})
        body = (f"## Behavior\n\nPrepares Savior `{candidate}` across npm, Rust, and Claude plugin metadata.\n\n"
                "Merging does not publish or create a tag. After required checks and review, "
                f"a maintainer must push `v{candidate}` on the merged main commit.\n\n"
                "## Verification\n\nVerification, commit checks, and three-platform release qualification "
                "are dispatched for this PR. Runtime Rust modules and adapters are unchanged.\n\n"
                "## Manual steps\n\nCheck release prerequisites and credentials before tagging. "
                "Publish only the reviewed, merged revision; do not move an existing tag.\n")
        pr = api("api", "--method", "POST", f"repos/{repository}/pulls", payload={
            "title": f"chore: prepare release {candidate}", "body": body,
            "head": branch, "base": "main"})
        api("api", "--method", "POST", f"repos/{repository}/issues/{pr['number']}/labels",
            payload={"labels": ["release"]})
    if (pr["head"]["ref"] != branch or pr["head"]["repo"]["full_name"] != repository
            or pr["base"]["ref"] != "main"):
        raise ValueError("existing release pull request does not match")
    package = api("api", "--method", "GET", f"repos/{repository}/contents/package.json",
                  "-f", f"ref={pr['head']['sha']}")
    if json.loads(base64.b64decode(package["content"]))["version"] != candidate:
        raise ValueError("existing release pull request version mismatch")
    # Pin the requested head so a branch update cannot silently change the checks.
    for workflow in ("verify.yml", "commit-messages.yml", "qualification.yml"):
        api("api", "--method", "POST", f"repos/{repository}/actions/workflows/{workflow}/dispatches",
            payload={"ref": branch, "inputs": {"pr_number": str(pr["number"]), "head_sha": pr["head"]["sha"]}})
    print(pr["html_url"])
    return pr


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("release preparation must run from main")
    prepare(Path.cwd(), args.version, os.environ["GITHUB_REPOSITORY"])


if __name__ == "__main__":
    main()
