"""Local inference estimates and immutable, revision-bound spending quotes."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time
import zipfile

from budget import INPUT_RATE, OUTPUT_RATE, RESERVED_INPUT_TOKENS, cost

PRICING = {'version': 'deepseek-us-standard-2026-11-04', 'checked': '2026-11-04',
           'input_nanodollars': INPUT_RATE, 'output_nanodollars': OUTPUT_RATE,
           'source': 'https://aws.amazon.com/bedrock/pricing/'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def configuration():
    root = Path(__file__).parent
    files = sorted([*root.glob('*.py'), *root.glob('*.cjs')])
    files.append(root.parent.parent/'.github/workflows/adversarial-review.yml')
    return digest({str(p.relative_to(root.parent.parent)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files})


def item(name, inputs, outputs):
    return {'phase': name, 'calls': len(inputs),
            'estimated_nanodollars': sum(cost(n, outputs) for n in inputs),
            'reserved_nanodollars': len(inputs)*cost(RESERVED_INPUT_TOKENS, outputs)}


def estimate(manifest):
    chunks = manifest['chunks']
    fresh = [c for c in chunks if not c.get('cached_assessment')]
    return [item('assessment', [c['input_tokens'] for c in fresh], 1024),
            item('verification', [48000]*len(fresh), 1536),
            item('assessment/verification retries', [48000]*(2*len(fresh)), 4096),
            # One group per chunk plus a final summary covers the largest grouping.
            item('summary and retry', [48000]*(2*(len(chunks)+1)), 256)]


def qualification_estimate():
    from qualify import CASES, fixture
    from adversarial_review import review_diff
    count = len(CASES)
    summaries = 0
    for strategy in ('baseline', 'adaptive'):
        for disposition in ('APPROVE', 'REQUEST_CHANGES'):
            fixtures = [fixture(i, c) for i, c in enumerate(CASES) if c[3] == disposition]
            plan = review_diff(''.join(d for d, _ in fixtures), [f for _, f in fixtures], strategy=strategy, dry_run=True)
            chunks = len(plan['manifest']['chunks'])
            count += chunks
            summaries += 2*(chunks+1)
    estimated = count*(cost(48000, 1024)+cost(48000, 1536)+2*cost(48000, 4096))+summaries*cost(48000, 256)
    reserved = count*(cost(RESERVED_INPUT_TOKENS, 1024)+cost(RESERVED_INPUT_TOKENS, 1536)+2*cost(RESERVED_INPUT_TOKENS, 4096))+summaries*cost(RESERVED_INPUT_TOKENS, 256)
    return {'phase': 'qualification', 'calls': 4*count+summaries,
            'estimated_nanodollars': estimated, 'reserved_nanodollars': reserved}


def create(manifests, summary, qualification, *, now=None):
    from chunk_plan import bounded_strategy
    if os.environ['BEDROCK_REVIEW_MODEL'] != 'deepseek.v3.2' or os.environ['AWS_REGION'] != 'us-east-1':
        raise ValueError('unsupported_pricing')
    now = int(time.time() if now is None else now)
    preferred = qualification['strategy'] if qualification else 'baseline'
    selected = bounded_strategy(preferred, manifests)
    # Before qualification picks a strategy, quote the more expensive feasible plan.
    options = [selected] if qualification else [s for s in manifests if len(manifests[s]['chunks']) <= 512]
    selected = max(options, key=lambda s: sum(i['reserved_nanodollars'] for i in estimate(manifests[s])))
    items = estimate(manifests[selected])
    items.insert(0, {'phase': 'qualification', 'calls': 0, 'estimated_nanodollars': 0, 'reserved_nanodollars': 0}
                 if qualification else qualification_estimate())
    binding = {'repository': os.environ['GITHUB_REPOSITORY'], 'pr': int(os.environ['PR_NUMBER']),
               'base': os.environ['BASE_SHA'], 'head': os.environ['HEAD_SHA'], 'configuration': configuration(),
               'model': os.environ['BEDROCK_REVIEW_MODEL'], 'region': os.environ['AWS_REGION'],
               'pricing': PRICING,
               # Cache availability changes remaining cost, not the quoted evidence.
               'plans': {s: digest({**m, 'chunks': [{k:v for k,v in c.items() if k != 'cached_assessment'}
                                                  for c in m['chunks']]}) for s, m in manifests.items()}}
    maximum = sum(i['reserved_nanodollars'] for i in items)
    quote = {'schema': 1, 'binding': binding, 'issued_at': now, 'expires_at': now+86400,
             'items': items, 'strategy': selected, 'chunks': len(manifests[selected]['chunks']),
             'cached_assessments': sum(bool(c.get('cached_assessment')) for c in manifests[selected]['chunks']),
             'estimated_nanodollars': sum(i['estimated_nanodollars'] for i in items),
             'cap_nanodollars': ((maximum+9_999_999)//10_000_000)*10_000_000,
             'pilot_committed_nanodollars': summary['pilot_committed_nanodollars']}
    quote['id'] = digest(quote)
    return quote


def validate(quote, *, now=None):
    now = int(time.time() if now is None else now)
    if (quote.get('schema') != 1 or quote.get('id') != digest({k:v for k,v in quote.items() if k != 'id'})
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
