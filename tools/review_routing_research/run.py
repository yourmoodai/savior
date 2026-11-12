"""Reproducible offline replay. This module has no paid inference entry point."""
import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import resource
import sys
import tracemalloc

from packing import compare, digest, verify_binding
from local_model import CONFIG, server, setup
from routers import metrics, route, train
from research_cases import cases

ROOT = Path(__file__).resolve().parents[2]


def dataset():
    stored = json.loads((ROOT/'docs/research/reviewer-designs-results.json').read_text())
    observed = {c['case']: c for c in stored['cases']}
    rows = []
    for case in cases():
        family = case.get('family', 'qualification')
        if case['id'] in ('qualification_6', 'qualification_7'):
            family = 'spending_boundary'
        # Paths and identifiers distinguish fixtures, not escalation necessity.
        diff = '\n'.join(line for line in case['diff'].splitlines()
                         if not line.startswith(('diff --git', '---', '+++')))
        blank = {'diff': diff, 'context': case['context'], 'findings': [], 'incomplete': False,
                 'invalid': False, 'missing_context': False, 'contradiction': False, 'unsupported': False}
        recorded = observed[case['id']]['arms']['single_role']
        evidence = {**blank, 'findings': [{k: v for k, v in f.items() if k != 'file'} for f in recorded['findings']],
                    'incomplete': not recorded['complete']}
        # The trial did not adjudicate all finding semantics. Only clean benign outcomes have a known label.
        label = False if case['oracle']['resolved'] and case['expected'] == 'APPROVE' and not evidence['findings'] and not evidence['incomplete'] else None
        rows.append({'id': case['id']+'/observed', 'family': family, 'source': 'observed_deepseek_single_role',
                     'label': label, 'label_basis': 'benign_oracle' if label is False else 'unresolved_finding_semantics', 'evidence': evidence})
        if case.get('source') != 'new' or not case['oracle']['resolved']:
            continue
        for mode in ('clean', 'incomplete', 'unsupported', 'contradiction'):
            evidence = {**blank, **({mode: True} if mode != 'clean' else {})}
            if mode == 'unsupported':
                evidence['findings'] = [{'details': 'synthetic claim without supporting evidence'}]
            rows.append({'id': case['id']+'/'+mode, 'family': family, 'source': 'simulated_review',
                         'label': mode != 'clean' or case['expected'] == 'REQUEST_CHANGES',
                         'label_basis': 'mandatory_policy' if mode != 'clean' else 'executable_oracle', 'evidence': evidence})
    return rows


def benchmark(client=None):
    started = datetime.now(timezone.utc).isoformat()
    rows = dataset()
    simulations = [r for r in rows if r['source'] == 'simulated_review']
    folds = {family: train([r for r in simulations if r['family'] != family]) for family in {r['family'] for r in rows}}
    outcomes = {}
    for kind in ('rules', 'classifier', 'local_llm'):
        results = [route(kind, row['evidence'], folds[row['family']], client) for row in rows]
        outcomes[kind] = {'results': results, 'metrics_by_source': {
            source: metrics([r for r in rows if r['source'] == source],
                            [p for r, p in zip(rows, results) if r['source'] == source])
            for source in sorted({r['source'] for r in rows})}}
    scale = 1 if sys.platform == 'darwin' else 1024
    return {'schema': 1, 'started_utc': started, 'advisory_only': True, 'paid_calls': 0, 'measured_savings': None,
            'host': {'platform': platform.platform(), 'machine': platform.machine()},
            'dataset_fingerprint': digest(rows), 'model_config': CONFIG,
            'source_fingerprint': digest({p.name: p.read_text() for p in sorted(Path(__file__).parent.glob('*.py'))}),
            'local_runtime': client.runtime if client else None,
            'local_startup_seconds': client.startup_seconds if client else None,
            'process_peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*scale,
            'folds': folds, 'cases': rows, 'routers': outcomes,
            'limitations': ['classifier trained only on simulated reviews', 'no matched cheap/strong-model evidence',
                            'finding semantics unresolved for observed defective cases', 'no production promotion']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('setup', 'packing', 'benchmark', 'replay'))
    parser.add_argument('--model-dir', default='.cache/cpu-router')
    parser.add_argument('--local-llm', action='store_true')
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--diff', type=Path)
    parser.add_argument('--binding', type=Path)
    parser.add_argument('--output', type=Path, default=Path('.cache/cpu-routing-report.json'))
    args = parser.parse_args()
    if args.command == 'setup':
        setup(args.model_dir, args.debug)
        return
    if args.command in ('packing', 'replay'):
        if not args.diff:
            parser.error('--diff is required')
        diff = args.diff.read_text()
        if args.command == 'replay':
            if not args.binding:
                parser.error('--binding is required')
            result = json.loads(args.binding.read_text())
            verify_binding(result, diff)
        else:
            result = compare(diff)
    else:
        try:
            with server(args.model_dir) if args.local_llm else nullcontext(None) as client:
                result = benchmark(client)
        except (ValueError, OSError) as error:
            result = benchmark()
            result['local_setup_failure'] = str(error)
        scale = 1 if sys.platform == 'darwin' else 1024
        result['children_peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss*scale
        result['python_peak_allocated_bytes'] = tracemalloc.get_traced_memory()[1] if tracemalloc.is_tracing() else None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True)+'\n')
    print(json.dumps({'output': str(args.output), 'paid_calls': 0}))


if __name__ == '__main__':
    main()
