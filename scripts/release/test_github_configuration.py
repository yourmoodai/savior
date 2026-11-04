"""Exercise release policy planning without modifying GitHub."""

import base64
import copy
from contextlib import redirect_stdout
import io
import unittest

from configure_github import configure, tag_rulesets


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.rules = []
        self.environment = {"deployment_branch_policy": None, "protection_rules": [
            {"type": "required_reviewers", "prevent_self_review": True,
             "reviewers": [{"type": "User", "reviewer": {"id": 123}}]}]}
        self.policies = []
        self.writes = []

    def api(self, endpoint, method="GET", payload=None):
        if method != "GET":
            self.writes.append((endpoint, method, payload))
            if endpoint.endswith("/rulesets"):
                self.rules.append({**copy.deepcopy(payload), "id": len(self.rules) + 1,
                                   "source": "example/repo"})
            elif endpoint.endswith("/environments/release"):
                self.environment["deployment_branch_policy"] = payload["deployment_branch_policy"]
            elif endpoint.endswith("/deployment-branch-policies"):
                self.policies.append(copy.deepcopy(payload))
            return {}
        if "/rulesets?" in endpoint:
            return self.rules
        if "/rulesets/" in endpoint:
            return self.rules[int(endpoint.rsplit("/", 1)[1]) - 1]
        if endpoint.endswith("/environments/release"):
            return self.environment
        if "/deployment-branch-policies?" in endpoint:
            return {"branch_policies": self.policies}
        if endpoint.endswith("/commits/main"):
            return {"sha": "a" * 40}
        if "/contents/" in endpoint:
            return {"content": base64.b64encode(b"old workflow").decode()}
        self.fail(endpoint)

    def configure(self, **kwargs):
        with redirect_stdout(io.StringIO()):
            return configure("example/repo", api=self.api, **kwargs)

    def test_preview_does_not_write_and_apply_is_idempotent(self):
        self.assertEqual(len(self.configure()), 4)
        self.assertEqual(self.writes, [])
        self.configure(apply=True)
        self.assertEqual(len(self.writes), 4)
        self.assertEqual(self.configure(apply=True), [])
        self.assertEqual(len(self.writes), 4)
        update = next(payload for endpoint, _, payload in self.writes
                      if endpoint.endswith("/environments/release"))
        self.assertEqual(update["reviewers"], [{"type": "User", "id": 123}])
        self.assertTrue(update["prevent_self_review"])

    def test_creation_privileges_do_not_allow_tag_mutation(self):
        creation, immutable = tag_rulesets()
        self.assertEqual({actor["actor_id"] for actor in creation["bypass_actors"]}, {2, 5})
        self.assertEqual(immutable["bypass_actors"], [])
        self.assertEqual({rule["type"] for rule in immutable["rules"]}, {"update", "deletion"})

    def test_early_enforcement_and_unexpected_policies_fail_before_writes(self):
        with self.assertRaisesRegex(ValueError, "reach main"):
            self.configure(apply=True, enforce=True)
        self.assertEqual(self.writes, [])
        self.environment["deployment_branch_policy"] = {"custom_branch_policies": True}
        self.policies = [{"name": "main", "type": "branch"}]
        with self.assertRaisesRegex(ValueError, "unexpected"):
            self.configure(apply=True)
        self.assertEqual(self.writes, [])
