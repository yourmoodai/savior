"""Local inference estimates and immutable, revision-bound spending quotes."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time
import zipfile


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def configuration():
    from result_cache import SOURCES, source_identity
    root = Path(__file__).parent
    files = [root/name for name in sorted(set(SOURCES) | {'run_ci.py', 'preflight.py', 'cost_policy.py', 'requirements.txt'})]
    files.extend(sorted(root.glob('*.cjs')))
    files.append(root.parent.parent/'.github/workflows/adversarial-review.yml')
    files.append(root.parent.parent/'.github/workflows/pr-validation.yml')
    return digest({str(p.relative_to(root.parent.parent)): source_identity(p) for p in files})


def item(name, inputs, outputs, role='elephant'):
    from model_catalog import selected, cost
    model = selected(role)
    attempts = (3 if model['adapter'] == 'chat_completions' else 1)*model.get('json_attempts', 1)
    return {'phase': name, 'role': role, 'calls': len(inputs)*attempts,
            'estimated_nanodollars': sum(cost(model, n, outputs) for n in inputs),
            'reserved_nanodollars': len(inputs)*attempts*cost(model, model['context_tokens'], outputs)}


def estimate(manifest):
    chunks = manifest['chunks']
    fresh = [c for c in chunks if not c.get('cached_assessment')]
    items = []
    for role in ('correctness', 'security', 'compatibility'):
        items.extend([item(role, [c['input_tokens'] for c in fresh], 1024, role),
                      item(role+' retry', [48000]*len(fresh), 4096, role),
                      item(role+' verification', [48000]*len(fresh)*2, 1536, 'verification'),
                      item(role+' verification retry', [48000]*len(fresh)*2, 4096, 'verification')])
    return items


def qualification_estimate():
    from qualify import CASES, fixture
    from adversarial_review import review_diff
    count = len(CASES)
    for strategy in ('baseline', 'adaptive'):
        for disposition in ('APPROVE', 'REQUEST_CHANGES'):
            fixtures = [fixture(i, c) for i, c in enumerate(CASES) if c[3] == disposition]
            from source_graph import snapshots, fixture_snapshots
            fixture_diff = ''.join(d for d, _ in fixtures)
            token = snapshots.set(fixture_snapshots(fixture_diff))
            try:
                plan = review_diff(fixture_diff, [f for _, f in fixtures], strategy=strategy, dry_run=True)
            finally:
                snapshots.reset(token)
            count += len(plan['manifest']['chunks'])
    items = estimate({'chunks': [{'input_tokens': 48000}]*count})
    return {'phase': 'qualification', **{k: sum(i[k] for i in items) for k in
            ('calls', 'estimated_nanodollars', 'reserved_nanodollars')}}


def qualified_strategy(manifests, qualification):
    from chunk_plan import ASSESSMENT_LIMIT
    if not qualification or not qualification.get('qualified'):
        return None
    options = [name for name, result in qualification.get('comparison', {}).items()
               if result.get('passed') is True and name in manifests
               and len(manifests[name]['chunks']) <= ASSESSMENT_LIMIT
               and not any(c.get('omitted_support') for c in manifests[name]['chunks'])]
    if not options:
        from review_plan import PlanningError
        raise PlanningError('no qualified feasible plan')
    return min(options, key=lambda name: (sum(i['reserved_nanodollars'] for i in estimate(manifests[name])), name))


def plan_digest(manifest):
    return digest({**manifest, 'chunks': [{k: v for k, v in c.items() if k != 'cached_assessment'}
                                        for c in manifest['chunks']]})


def execution_strategy(quote, manifests, qualification):
    validate(quote)
    selected = quote.get('strategy')
    if selected is None or selected not in manifests:
        raise ValueError('qualification required; execution quote unavailable')
    if (quote['binding']['qualification'] != digest(qualification_identity(qualification))
            or quote['binding']['plans'][selected] != plan_digest(manifests[selected])):
        raise ValueError('quoted plan or qualification changed')
    if not qualification.get('comparison', {}).get(selected, {}).get('passed'):
        raise ValueError('quoted strategy is not qualified')
    return selected


def qualification_identity(qualification):
    if qualification is None:
        return None
    return {k: v for k, v in qualification.items() if k not in ('budget', 'cache_hit')}


def create(manifests, summary, qualification, *, now=None):
    from review_plan import require_feasible
    require_feasible(manifests)
    from model_catalog import manifest, selected as selected_model, cost
    routing = manifest()
    if os.environ['AWS_REGION'] != 'us-east-1':
        raise ValueError('unsupported_pricing')
    now = int(time.time() if now is None else now)
    selected = qualified_strategy(manifests, qualification)
    items = estimate(manifests[selected]) if selected else []
    items.insert(0, {'phase': 'qualification', 'calls': 0, 'estimated_nanodollars': 0, 'reserved_nanodollars': 0}
                 if qualification else qualification_estimate())
    binding = {'repository': os.environ['GITHUB_REPOSITORY'], 'pr': int(os.environ['PR_NUMBER']),
               'base': os.environ['BASE_SHA'], 'head': os.environ['HEAD_SHA'], 'configuration': configuration(),
               'model': 'elephant-goldfish', 'region': os.environ['AWS_REGION'],
               'routing': routing, 'pricing': {n: m['pricing'] for n, m in routing['models'].items()},
               # Cache availability changes remaining cost, not the quoted evidence.
               'qualification': digest(qualification_identity(qualification)),
               'plans': {s: plan_digest(m) for s, m in manifests.items()}}
    maximum = sum(i['reserved_nanodollars'] for i in items)
    needs_call = selected is None or any(not c.get('cached_assessment') for c in manifests[selected]['chunks'])
    first_model = selected_model('correctness')
    next_reservation = cost(first_model, first_model['context_tokens'], 1024) if needs_call else 0
    quote = {'schema': 3, 'binding': binding, 'issued_at': now, 'expires_at': now+86400,
             'phase': 'review' if selected else 'qualification_required',
             'projection_informational': True, 'next_call_reserved_nanodollars': next_reservation,
             'items': items, 'strategy': selected, 'chunks': len(manifests[selected]['chunks']) if selected else 0,
             'cached_assessments': sum(bool(c.get('cached_assessment')) for c in manifests[selected]['chunks']) if selected else 0,
             'estimated_nanodollars': sum(i['estimated_nanodollars'] for i in items),
             'projected_nanodollars': maximum,
             'pilot_committed_nanodollars': summary['pilot_committed_nanodollars']}
    quote['id'] = digest(quote)
    return quote


def apply_policy(quote, allowance):
    from cost_policy import check
    from budget import BudgetExhausted
    quote = {**quote, 'policy': allowance, 'blocked_reason': None}
    try:
        check(quote['next_call_reserved_nanodollars'], allowance)
    except BudgetExhausted:
        quote['blocked_reason'] = 'cost_limit'
    quote['id'] = digest({k:v for k,v in quote.items() if k != 'id'})
    return quote


def validate(quote, *, now=None):
    now = int(time.time() if now is None else now)
    if (quote.get('schema') != 3 or quote.get('id') != digest({k:v for k,v in quote.items() if k != 'id'})
            or quote.get('projection_informational') is not True
            or type(quote.get('next_call_reserved_nanodollars')) is not int or quote['next_call_reserved_nanodollars'] < 0
            or type(quote.get('issued_at')) is not int or type(quote.get('expires_at')) is not int
            or quote['expires_at']-quote['issued_at'] != 86400 or not quote['issued_at'] <= now < quote['expires_at']):
        raise ValueError('quote expired or invalid')


def inspect_archive(data):
    if len(data) > 1_000_000:  # 1 MB quote archive limit
        raise ValueError('quote archive too large')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.namelist() != ['quote.json'] or archive.getinfo('quote.json').file_size > 100_000:  # 100 kB quote limit
            raise ValueError('invalid quote archive')
        quote = json.loads(archive.read('quote.json'))
    validate(quote)
    return quote


if __name__ == '__main__':
    print(json.dumps(inspect_archive(sys.stdin.buffer.read(1_000_001))))
