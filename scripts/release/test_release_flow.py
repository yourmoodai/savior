"""Exercise release authorization and preparation without publishing artifacts."""

import base64
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from pr_context import resolve_context
from prepare import prepare, version_changes
from version_gate import parse_version, reject_existing_release, release_version, validate_tag


ROOT = Path(__file__).resolve().parents[2]


def fixture(root):
    for name in ("package.json", "package-lock.json", "Cargo.toml", "Cargo.lock",
                 ".claude-plugin/plugin.json", "crates/core/Cargo.toml",
                 "crates/node/Cargo.toml", "crates/cli/Cargo.toml",
                 "crates/review/Cargo.toml", "crates/lab/Cargo.toml", "tools/commit_check/Cargo.toml"):
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
    current = release_version(root)
    for name in ('package.json','package-lock.json','Cargo.toml','Cargo.lock','.claude-plugin/plugin.json'):
        path = root/name
        path.write_text(path.read_text().replace('"'+current+'"', '"0.1.0"'))


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        fixture(self.root)

    def test_versions_and_dependency_resolutions_survive_preparation(self):
        old_lock = json.loads((self.root / "package-lock.json").read_text())
        old_cargo = (self.root / "Cargo.lock").read_text()
        changes = version_changes(self.root, "0.2.0-rc.1")
        for name, content in changes.items():
            (self.root / name).write_text(content)
        self.assertEqual(release_version(self.root), "0.2.0-rc.1")
        lock = json.loads(changes["package-lock.json"])
        old_lock["version"] = lock["version"]
        old_lock["packages"][""]["version"] = lock["version"]
        self.assertEqual(lock, old_lock)
        self.assertEqual(changes["Cargo.lock"].replace('version = "0.2.0-rc.1"', 'version = "0.1.0"'), old_cargo)

    def test_reject_inconsistent_source_versions(self):
        path = self.root / ".claude-plugin/plugin.json"
        data = json.loads(path.read_text())
        data["version"] = "9.0.0"
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "disagree"):
            version_changes(self.root, "0.2.0")

    def test_reject_inconsistent_cargo_lock_version(self):
        path = self.root / "Cargo.lock"
        path.write_text(path.read_text().replace('name = "savior"\nversion = "0.1.0"',
                                                'name = "savior"\nversion = "0.0.1"'))
        with self.assertRaisesRegex(ValueError, "disagree"):
            release_version(self.root)

    def test_reject_unsafe_or_non_increasing_versions(self):
        for value in ("v0.2.0", "01.2.3", "0.2.0-01", "0.2.0;touch nope",
                      "$(whoami)", "0.2.0\n", "0.1.0", "0.0.9", "0.1.0+build"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                version_changes(self.root, value)

    def test_semver_precedence(self):
        ordered = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
                   "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0", "1.0.1"]
        self.assertEqual(sorted(ordered, key=parse_version), ordered)

    def test_tag_authorization_uses_real_git_ancestry(self):
        def git(*args, input=None, check=True):
            identity = {"GIT_AUTHOR_NAME": "release fixture", "GIT_COMMITTER_NAME": "release fixture",
                        "GIT_AUTHOR_EMAIL": "fixture@example.invalid", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
            return subprocess.run(["git", *args], cwd=self.root, input=input, text=True,
                                  capture_output=True, check=check, env={**os.environ, **identity}).stdout.strip()
        git("init", "-q")
        tree = git("mktree", input="")
        main = git("commit-tree", tree, input="synthetic main\n")
        outside = git("commit-tree", tree, input="synthetic outside\n")
        git("update-ref", "refs/remotes/origin/main", main)
        git("update-ref", "refs/heads/fixture", main)
        git("symbolic-ref", "HEAD", "refs/heads/fixture")
        git("update-ref", "refs/tags/v0.1.0", main)
        self.assertEqual(validate_tag(self.root, "refs/tags/v0.1.0", main), "0.1.0")
        annotated = git("mktag", input=(f"object {main}\ntype commit\ntag v0.1.0\n"
                                       "tagger release fixture <fixture@example.invalid> 1700000000 +0000\n\nsynthetic release\n"))
        git("update-ref", "refs/tags/v0.1.0", annotated)
        self.assertEqual(validate_tag(self.root, "refs/tags/v0.1.0", main), "0.1.0")
        for ref, sha in (("refs/heads/main", main), ("refs/tags/v0.2.0", main),
                         ("refs/tags/v0.1.0", outside)):
            with self.subTest(ref=ref, sha=sha), self.assertRaises(ValueError):
                validate_tag(self.root, ref, sha)
        git("update-ref", "refs/tags/v0.1.0", outside)
        git("update-ref", "refs/heads/fixture", outside)
        with self.assertRaises(subprocess.CalledProcessError):
            validate_tag(self.root, "refs/tags/v0.1.0", outside)

    @patch("version_gate.subprocess.check_output")
    def test_existing_draft_or_published_release_blocks(self, output):
        for draft in (True, False):
            output.return_value = json.dumps([[{"tag_name": "v0.1.0", "draft": draft}]])
            with self.assertRaisesRegex(ValueError, "immutable"):
                reject_existing_release("example/project", "0.1.0")
        output.side_effect = subprocess.CalledProcessError(1, "gh")
        with self.assertRaises(subprocess.CalledProcessError):
            reject_existing_release("example/project", "0.1.0")


def pull_request():
    return {"state": "open", "number": 7, "html_url": "https://example.test/pull/7",
            "title": "chore: prepare release 0.2.0",
            "base": {"ref": "main", "sha": "b" * 40, "repo": {"full_name": "example/project"}},
            "head": {"ref": "release/v0.2.0", "sha": "a" * 40, "repo": {"full_name": "example/project"}}}


class ContextTests(unittest.TestCase):
    def test_generated_pr_checks_require_exact_current_head(self):
        pr = pull_request()
        event = {"inputs": {"pr_number": "7", "head_sha": "a" * 40}}
        context = resolve_context(event, "example/project", "a" * 40, lambda _: pr)
        self.assertEqual(context["title"], pr["title"])
        for boundary in ("head", "base"):
            invalid = copy.deepcopy(pr)
            invalid[boundary]["repo"]["full_name"] = "other/project"
            with self.assertRaises(ValueError):
                resolve_context(event, "example/project", "a" * 40, lambda _: invalid)
        for field, value in (("state", "closed"), ("head", {**pr["head"], "sha": "c" * 40}),
                             ("base", {**pr["base"], "ref": "other"})):
            invalid = {**pr, field: value}
            with self.assertRaises(ValueError):
                resolve_context(event, "example/project", "a" * 40, lambda _: invalid)
        with self.assertRaises(ValueError):
            resolve_context(event, "example/project", "c" * 40, lambda _: pr)

    def test_ordinary_events_keep_their_native_context(self):
        def no_lookup(_):
            self.fail("ordinary events must not query a generated PR")
        pr = pull_request()
        context = resolve_context({"pull_request": pr, "number": 7}, "example/project", "merge", no_lookup)
        self.assertEqual(context["head"], "a" * 40)
        self.assertEqual(resolve_context({"before": "before"}, "example/project", "after", no_lookup)["head"], "after")


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        fixture(self.root)
        self.calls = []
        self.existing = False
        self.stale = False
        self.tags = []
        self.releases = []

    def api(self, *args, payload=None):
        self.calls.append((args, payload))
        endpoint = next((arg for arg in args if arg.startswith("repos/")), "graphql")
        if endpoint.endswith("/tags"):
            return [self.tags]
        if endpoint.endswith("/releases"):
            return [self.releases]
        if endpoint.endswith("/pulls"):
            return pull_request() if payload else ([pull_request()] if self.existing else [])
        if "/matching-refs/" in endpoint:
            return [{"ref": "refs/heads/release/v0.2.0"}] if self.stale else []
        if endpoint.endswith("/labels") and payload is None:
            return [[{"name": "release"}]]
        if endpoint.endswith("/contents/package.json"):
            return {"content": base64.b64encode(b'{"version":"0.2.0"}').decode()}
        return {}

    def run_prepare(self):
        with patch("prepare.subprocess.check_output", return_value="b" * 40), patch("builtins.print"):
            return prepare(self.root, "0.2.0", "example/project", api=self.api)

    def test_create_version_pr_and_dispatch_checks_without_tags(self):
        self.run_prepare()
        refs = [payload["ref"] for _, payload in self.calls if payload and "ref" in payload]
        self.assertIn("refs/heads/release/v0.2.0", refs)
        self.assertFalse(any(ref.startswith("refs/tags/") for ref in refs))
        dispatches = [payload for args, payload in self.calls if any("/dispatches" in arg for arg in args)]
        self.assertEqual(len(dispatches), 3)
        self.assertTrue(all(p["inputs"] == {"pr_number": "7", "head_sha": "a" * 40} for p in dispatches))
        commit = next(payload for args, payload in self.calls if args[:2] == ("api", "graphql"))
        additions = commit["variables"]["input"]["fileChanges"]["additions"]
        self.assertEqual(len(additions), 5)

    def test_duplicate_request_reuses_pr_without_writing(self):
        self.existing = True
        self.run_prepare()
        writes = [args for args, _ in self.calls if "POST" in args]
        self.assertEqual(len(writes), 3)
        self.assertTrue(all(any("/dispatches" in arg for arg in args) for args in writes))

    def test_stale_branch_blocks_before_writes(self):
        self.stale = True
        with self.assertRaisesRegex(ValueError, "without an open"):
            self.run_prepare()
        self.assertFalse(any(payload for _, payload in self.calls))

    def test_interrupted_branch_and_version_commit_resume_without_overwrite(self):
        original=self.api
        changes=version_changes(self.root,'0.2.0')
        for committed in (False,True):
            self.calls.clear()
            def api(*args,payload=None):
                endpoint=next((arg for arg in args if arg.startswith('repos/')),'graphql')
                if '/matching-refs/' in endpoint:
                    return [{'ref':'refs/heads/release/v0.2.0','object':{'sha':('a' if committed else 'b')*40}}]
                if '/compare/' in endpoint:
                    return {'status':'ahead','ahead_by':1,'behind_by':0,'files':[{'filename':name} for name in changes]}
                if committed and '/contents/' in endpoint:
                    path=endpoint.split('/contents/')[1]
                    return {'content':base64.b64encode(changes[path].encode()).decode()}
                return original(*args,payload=payload)
            with patch('prepare.subprocess.check_output',return_value='b'*40),patch('builtins.print'):
                prepare(self.root,'0.2.0','example/project',api=api)
            self.assertFalse(any(payload and 'ref' in payload and payload['ref'].startswith('refs/') for _,payload in self.calls))
            commits=[payload for args,payload in self.calls if args[:2]==('api','graphql')]
            self.assertEqual(len(commits),0 if committed else 1)
            self.assertTrue(any('/pulls' in arg and payload for args,payload in self.calls for arg in args))

    def test_existing_tag_or_release_blocks_before_writes(self):
        for kind in ("tags", "releases"):
            self.calls.clear()
            setattr(self, kind, [{"name": "v0.2.0", "tag_name": "v0.2.0"}])
            with self.assertRaises(ValueError):
                self.run_prepare()
            self.assertFalse(any(payload for _, payload in self.calls))
            setattr(self, kind, [])


class WorkflowBoundaryTests(unittest.TestCase):
    def test_only_tag_events_can_enter_production_workflow(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text()
        triggers = workflow.split("on:\n", 1)[1].split("concurrency:", 1)[0]
        self.assertEqual(triggers, "  push:\n    tags: ['v*', '!v*-dev.*']\n")
        self.assertIn("needs: gate", workflow)
        self.assertLess(workflow.index("version_gate.py --tag"), workflow.index("environment: release"))

    def test_qualification_cannot_publish_or_receive_production_credentials(self):
        workflow = (ROOT / ".github/workflows/qualification.yml").read_text()
        for forbidden in ("secrets.", "environment:", "contents: write", "publish.py", "  publish:"):
            self.assertNotIn(forbidden, workflow)
        self.assertIn("SAVIOR_RELEASE_SIGNING_KEY: ''", workflow)


if __name__ == "__main__":
    unittest.main()
