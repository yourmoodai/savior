"""Replay exact revisions through historical and dependency-aware packing offline."""
import time
import tracemalloc
from collections import Counter
from unittest.mock import patch

from packing import count_tokens
from adversarial_review import extract_git_diff, text_changes, review_messages, test_snapshot_context
from chunk_plan import historical_plan, anchors, parse, render
from source_graph import git_snapshots, digest, analyzer_versions
from review_plan import pack, feasible
from quote import estimate
from result_cache import fingerprint


def audit(base, head, test_evidence=None):
    diff, files = extract_git_diff(base, head)
    diff, omitted, excluded = text_changes(diff)
    sources = git_snapshots(base, head)
    context = test_snapshot_context(test_evidence, sources['new'].revision) if test_evidence is not None else ''
    def messages(units, support):
        return review_messages(render(units), sorted({u.path for u in units}), context, anchors(units),
                               supporting_context='\nSupporting evidence (context only):\n'+support if support else '')
    expected = Counter(a['id'] for a in anchors(parse(diff)))
    rows = {}
    with patch('bedrock_client.invoke', side_effect=AssertionError('offline audit forbids inference')), \
            patch('chat_transport.invoke_chat', side_effect=AssertionError('offline audit forbids inference')):
        for strategy in ('baseline', 'adaptive'):
            for kind in ('historical', 'dependency'):
                tracemalloc.start()
                began = time.monotonic()
                planned = historical_plan(diff, count_tokens, messages, strategy=strategy) if kind == 'historical' else pack(
                    diff, count_tokens, messages, sources, strategy)
                elapsed = time.monotonic()-began
                _, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                chunks = planned['chunks']
                ownership = Counter(a['id'] for c in chunks for a in anchors(c['units']))
                projections = estimate({'chunks': chunks})
                rows[kind+'_'+strategy] = {
                    'chunks': len(chunks), 'chunks_with_gaps': sum(bool(c['omitted_support']) for c in chunks),
                    'feasible': feasible({'chunks': chunks}),
                    'input_units': sum(c['input_tokens'] for c in chunks),
                    'support_bytes': sum(len(c['support'].encode()) for c in chunks),
                    'unique_primary_locations': len({a['id'] for c in chunks for a in anchors(c['units'])}),
                    'primary_locations': sum(len(anchors(c['units'])) for c in chunks),
                    'exact_primary_ownership': ownership == expected and all(n == 1 for n in ownership.values()),
                    'worst_case_reservation_nanodollars': sum(i['reserved_nanodollars'] for i in projections),
                    'request_estimate_nanodollars': sum(i['estimated_nanodollars'] for i in projections),
                    'seconds': elapsed, 'peak_python_bytes': peak,
                    'issues': sorted({g for c in chunks for g in c['omitted_support']}),
                    'plan_digest': planned.get('review_plan', {}).get('digest')}
    return {'schema': 1, 'snapshots': {s:v.revision for s,v in sources.items()}, 'diff_digest': digest(diff),
            'files': len(files), 'paid_calls': 0, 'usage_priced_cost_usd': 0, 'model_quality_measured': False,
            'expected_primary_locations': sum(expected.values()), 'omitted_binary_changes': omitted,
            'excluded_assets': excluded, 'analyzers': analyzer_versions(), 'harness_fingerprint': fingerprint(), 'plans': rows,
            'test_evidence': test_evidence,
            'cost_interpretation': 'hypothetical full-plan bounds at catalog prices; no calls, charges or settlement; infeasible plans are not executable alternatives',
            'interpretation': 'structural evidence and local python allocation measurements; unresolved plans remain blocked'}
