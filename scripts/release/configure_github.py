"""Preview or apply repository release protections using the configured gh account."""

import argparse
import base64
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def gh_api(endpoint, method="GET", payload=None):
    args = ["gh", "api", endpoint, "--method", method]
    if payload is not None:
        args += ["--input", "-"]
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def tag_rulesets():
    common = {"target": "tag", "enforcement": "active",
              "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}}}
    return [
        {**common, "name": "release tag creators", "rules": [{"type": "creation"}],
         # GitHub repository role IDs: maintain=2, admin=5.
         "bypass_actors": [{"actor_type": "RepositoryRole", "actor_id": role,
                            "bypass_mode": "always"} for role in (2, 5)]},
        {**common, "name": "immutable release tags",
         "rules": [{"type": "update"}, {"type": "deletion"}], "bypass_actors": []},
    ]


def production_rulesets(app_id):
    return [
        {'name':'required release qualification','target':'branch','enforcement':'active',
         'conditions':{'ref_name':{'include':['refs/heads/main'],'exclude':[]}},'bypass_actors':[],
         'rules':[{'type':'required_status_checks','parameters':{
             'strict_required_status_checks_policy':True,
             'required_status_checks':[{'context':name,'integration_id':app_id}
                                       for name in ('verification','qualification','check')]}},
             {'type':'pull_request','parameters':{'required_approving_review_count':1,
               'dismiss_stale_reviews_on_push':True,'require_code_owner_review':True,
               'require_last_push_approval':False,'required_review_thread_resolution':True}}]},
        {'name':'marketplace promotion writer','target':'branch','enforcement':'active',
         'conditions':{'ref_name':{'include':['refs/heads/marketplace'],'exclude':[]}},
         'bypass_actors':[{'actor_type':'Integration','actor_id':app_id,'bypass_mode':'always'}],
         'rules':[{'type':'creation'},{'type':'update'}]},
        {'name':'preserve marketplace history','target':'branch','enforcement':'active',
         'conditions':{'ref_name':{'include':['refs/heads/marketplace'],'exclude':[]}},
         'bypass_actors':[],'rules':[{'type':'deletion'},{'type':'non_fast_forward'}]},
    ]


def optional_environment(endpoint, api):
    try:
        return api(endpoint)
    except subprocess.CalledProcessError as error:
        if 'HTTP 404' not in (error.stderr or ''):
            raise
        return {}


def ensure_merged(repository, api):
    # Pin policy would break old default-branch workflows, including privileged reviewers.
    main = api(f"repos/{repository}/commits/main")["sha"]
    files = [*(ROOT / ".github").rglob("*.yml"), *(ROOT / ".github").rglob("*.yaml"),
             Path(__file__).resolve()]
    for file in files:
        relative = file.relative_to(ROOT).as_posix()
        try:
            remote = api(f"repos/{repository}/contents/{relative}?ref={main}")
        except subprocess.CalledProcessError as error:
            raise ValueError("updated automation must reach main before enforcing action pins") from error
        if base64.b64decode(remote["content"]) != file.read_bytes():
            raise ValueError("updated automation must reach main before enforcing action pins")


def configure(repository, apply=False, enforce=False, api=gh_api):
    prefix = f"repos/{repository}"
    if enforce:
        ensure_merged(repository, api)
    existing = {rule["name"]: rule for rule in api(prefix + "/rulesets?per_page=100")}
    changes = []
    desired_rules = tag_rulesets()
    if enforce:
        app = api('apps/github-actions')
        if app.get('slug') != 'github-actions':
            raise ValueError('unexpected status check integration')
        desired_rules += production_rulesets(app['id'])
    for desired in desired_rules:
        rule = existing.get(desired["name"])
        endpoint = prefix + "/rulesets"
        method = "POST"
        if rule:
            endpoint += f"/{rule['id']}"
            current = api(endpoint)
            if current.get("source") != repository or current.get("target") != desired['target']:
                raise ValueError("release ruleset name belongs to another policy")
            if all(current.get(key) == value for key, value in desired.items()):
                continue
            method = "PUT"
        changes.append((endpoint, method, desired))

    environment = api(prefix + "/environments/release")
    policy = {"protected_branches": False, "custom_branch_policies": True}
    if environment.get("deployment_branch_policy") != policy:
        update = {"deployment_branch_policy": policy}
        for rule in environment.get("protection_rules", []):
            if rule["type"] == "wait_timer":
                update["wait_timer"] = rule["wait_timer"]
            elif rule["type"] == "required_reviewers":
                update["prevent_self_review"] = rule.get("prevent_self_review", False)
                update["reviewers"] = [{"type": item["type"], "id": item["reviewer"]["id"]}
                                       for item in rule["reviewers"]]
        changes.append((prefix + "/environments/release", "PUT", update))
    policies = (api(prefix + "/environments/release/deployment-branch-policies?per_page=100")["branch_policies"]
                if (environment.get("deployment_branch_policy") or {}).get("custom_branch_policies") else [])
    if any(item["name"] != "v*" or item["type"] != "tag" for item in policies):
        raise ValueError("inspect unexpected release deployment policies before changing them")
    if not policies:
        changes.append((prefix + "/environments/release/deployment-branch-policies", "POST",
                        {"name": "v*", "type": "tag"}))

    if enforce:
        immutable = api(prefix+'/immutable-releases')
        if not immutable.get('enabled'):
            changes.append((prefix+'/immutable-releases','PUT',None))
        reviewer = api('user')['id']
        for name, branch, kind in [('release','v*','tag'),('marketplace','main','branch')]:
            endpoint = prefix+'/environments/'+name
            current = environment if name=='release' else optional_environment(endpoint,api)
            existing_reviewers = next((rule for rule in current.get('protection_rules',[])
                                      if rule['type']=='required_reviewers'),None)
            if not existing_reviewers or name=='marketplace' and current.get('deployment_branch_policy')!=policy:
                payload = {'deployment_branch_policy':policy,
                           'reviewers':[{'type':item['type'],'id':item['reviewer']['id']}
                                        for item in existing_reviewers['reviewers']] if existing_reviewers
                                        else [{'type':'User','id':reviewer}],
                           'prevent_self_review':existing_reviewers.get('prevent_self_review',False) if existing_reviewers else False}
                timer = next((r for r in current.get('protection_rules',[]) if r['type']=='wait_timer'),None)
                if timer:
                    payload['wait_timer'] = timer['wait_timer']
                changes.append((endpoint,'PUT',payload))
            if name=='marketplace':
                policies = api(endpoint+'/deployment-branch-policies?per_page=100')['branch_policies'] if (current.get('deployment_branch_policy') or {}).get('custom_branch_policies') else []
                if any(p['name']!=branch or p['type']!=kind for p in policies):
                    raise ValueError('inspect unexpected marketplace deployment policies')
                if not policies:
                    changes.append((endpoint+'/deployment-branch-policies','POST',{'name':branch,'type':kind}))
        permissions = api(prefix + "/actions/permissions")
        if not permissions.get("sha_pinning_required"):
            changes.append((prefix + "/actions/permissions", "PUT",
                            {**permissions, "sha_pinning_required": True}))
        workflow = api(prefix + "/actions/permissions/workflow")
        if workflow["default_workflow_permissions"] != "read":
            changes.append((prefix + "/actions/permissions/workflow", "PUT",
                            {**workflow, "default_workflow_permissions": "read"}))
    for endpoint, method, payload in changes:
        print(json.dumps({"method": method, "endpoint": endpoint, "payload": payload}))
        if apply:
            api(endpoint, method=method, payload=payload)
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes; default only previews")
    parser.add_argument("--enforce-after-merge", action="store_true",
                        help="also enforce required CI, code owners, immutable releases, and promotion approval")
    args = parser.parse_args()
    repository = subprocess.check_output(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        cwd=ROOT, text=True).strip()
    configure(repository, apply=args.apply, enforce=args.enforce_after_merge)


if __name__ == "__main__":
    main()
