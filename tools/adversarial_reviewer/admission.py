"""Offline admission of an exact diff; no provider calls or PR code execution."""
import argparse
import json
from pathlib import Path
import subprocess

from adversarial_review import extract_git_diff, review_diff, text_changes
from review_plan import feasible, require_feasible, restore
from source_graph import git_snapshots, snapshots


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--head', default='HEAD')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    head = subprocess.check_output(['git', 'rev-parse', '--verify', args.head+'^{commit}'], text=True).strip()
    source = git_snapshots(args.base, head)
    diff, files = extract_git_diff(args.base, head)
    text, omitted, _ = text_changes(diff)
    token = snapshots.set(source)
    plans = {}
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        for strategy in ('baseline', 'adaptive'):
            report = review_diff(diff, files, strategy=strategy, dry_run=True)
            (args.output/(strategy+'.json')).write_text(json.dumps(report, indent=2)+'\n')
            plans[strategy] = report.get('manifest', {})
        if omitted:
            raise ValueError('unsupported binary evidence')
        require_feasible(plans)
        for plan in plans.values():
            if feasible(plan):
                restore(plan['review_plan'], text, source)
    finally:
        snapshots.reset(token)
    print('offline admission passed for '+head)


if __name__ == '__main__':
    main()
