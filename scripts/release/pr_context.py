"""Resolve generated PR checks against GitHub's current PR metadata."""

import json
import os
from pathlib import Path
import re
import subprocess
import uuid


def resolve_context(event, repository, sha, lookup):
    inputs = event.get("inputs", {})
    number = inputs.get("pr_number", "")
    if not number:
        pr = event.get("pull_request", {})
        return {"base": pr.get("base", {}).get("sha", event.get("before", "")),
                "head": pr.get("head", {}).get("sha", sha),
                "title": pr.get("title", ""), "pr_number": str(event.get("number", ""))}
    if not re.fullmatch(r"[1-9][0-9]*", str(number)):
        raise ValueError("invalid pull request number")
    if inputs.get("head_sha") != sha:
        raise ValueError("dispatched revision changed")
    pr = lookup(number)
    if (pr["state"] != "open" or pr["base"]["ref"] != "main"
            or pr["base"]["repo"]["full_name"] != repository
            or pr["head"]["repo"]["full_name"] != repository
            or pr["head"]["sha"] != sha):
        raise ValueError("dispatched pull request does not match checkout")
    return {"base": pr["base"]["sha"], "head": pr["head"]["sha"],
            "title": pr["title"], "pr_number": str(number)}


def main():
    repository = os.environ["GITHUB_REPOSITORY"]
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())

    def lookup(number):
        return json.loads(subprocess.check_output(
            ["gh", "api", f"repos/{repository}/pulls/{number}"], text=True))

    context = resolve_context(event, repository, os.environ["GITHUB_SHA"], lookup)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        for key, value in context.items():
            # PR titles may contain newlines; delimiters must not come from the event.
            delimiter = uuid.uuid4().hex
            output.write(f"{key}<<{delimiter}\n{value}\n{delimiter}\n")


if __name__ == "__main__":
    main()
