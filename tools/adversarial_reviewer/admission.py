"""Offline admission of an exact diff; no provider calls or PR code execution."""
import argparse
import json
from pathlib import Path
import subprocess

from adversarial_review import extract_git_diff, review_diff, text_changes
from review_plan import feasible, require_feasible, restore
from source_graph import git_snapshots, snapshots
from model_catalog import ROLES, load, routing_override
from quote import sizing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--head', default='HEAD')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--candidate', choices=sorted(load()[0]['models']),
                        help='replay all review roles on one catalog model, offline')
    args = parser.parse_args()
    head = subprocess.check_output(['git', 'rev-parse', '--verify', args.head+'^{commit}'], text=True).strip()
    source = git_snapshots(args.base, head)
    diff, files = extract_git_diff(args.base, head)
    text, omitted, _ = text_changes(diff)
    token = snapshots.set(source)
    plans = {}
    route = routing_override.set({'schema': 1, 'roles': dict.fromkeys(ROLES, args.candidate)}
                                 if args.candidate else routing_override.get())
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        for strategy in ('baseline', 'adaptive'):
            report = review_diff(diff, files, strategy=strategy, dry_run=True)
            (args.output/(strategy+'.json')).write_text(json.dumps(report, indent=2)+'\n')
            plans[strategy] = report.get('manifest', {})
        summary = {strategy: sizing(plan) for strategy, plan in plans.items() if plan.get('chunks')}
        (args.output/'sizing.json').write_text(json.dumps(summary, indent=2)+'\n')
        if omitted:
            raise ValueError('unsupported binary evidence')
        require_feasible(plans)
        for plan in plans.values():
            if feasible(plan):
                restore(plan['review_plan'], text, source)
    finally:
        routing_override.reset(route)
        snapshots.reset(token)
    print('offline admission passed for '+head)


if __name__ == '__main__':
    main()
