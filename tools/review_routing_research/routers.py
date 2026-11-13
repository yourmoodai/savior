"""Advisory routing with mandatory escalation and family-isolated evaluation."""
import math
from collections import Counter
import re
import time

from packing import digest

SENSITIVE = re.compile(r'consent|protected|profile|protocol|reserv|charged|sanitize', re.I)
FEATURES = ('log_bytes', 'changed_rows', 'finding_count', 'incomplete', 'invalid',
            'missing_context', 'contradiction', 'unsupported', 'sensitive')


def features(evidence):
    return [math.log1p(len(evidence['diff'].encode()))/10,
            min(sum(r.startswith(('+', '-')) for r in evidence['diff'].splitlines()), 1000)/1000,
            min(len(evidence['findings']), 10)/10,
            *[float(bool(evidence[k])) for k in FEATURES[3:-1]],
            float(bool(SENSITIVE.search(evidence['diff']+' '+evidence['context'])))]


def mandatory(evidence):
    signals = features(evidence)
    return [name for name, value in zip(FEATURES[3:], signals[3:]) if value]


def sigmoid(value):
    return 1/(1+math.exp(-max(-40, min(40, value))))


def train(rows):
    """Fixed L2 logistic regression; no tuning against held-out families."""
    labeled = [r for r in rows if r['label'] is not None]
    if len({r['family'] for r in labeled}) < 4 or {r['label'] for r in labeled} != {False, True}:
        return None
    samples = [([1, *features(r['evidence'])], float(r['label'])) for r in labeled]
    weights = [0.0]*(len(FEATURES)+1)
    for _ in range(400):
        gradient = [0.0]*len(weights)
        for values, label in samples:
            error = sigmoid(sum(w*x for w, x in zip(weights, values)))-label
            for j, value in enumerate(values):
                gradient[j] += error*value/len(samples)
        weights = [w-0.2*(g+(0.01*w if j else 0)) for j, (w, g) in enumerate(zip(weights, gradient))]
    return {'weights': weights, 'features': FEATURES, 'threshold': 0.5,
            'training_families': sorted({r['family'] for r in labeled}),
            'training_fingerprint': digest(labeled)}


def route(kind, evidence, model=None, client=None):
    started = time.monotonic()
    reasons = mandatory(evidence)
    failure = None
    try:
        if kind == 'rules':
            raw = 'escalate' if reasons else 'retain_cheap_review'
        elif kind == 'classifier':
            if model is None:
                raise ValueError('classifier unevaluable')
            score = sigmoid(sum(w*x for w, x in zip(model['weights'], [1, *features(evidence)])))
            raw = 'escalate' if score >= model['threshold'] else 'retain_cheap_review'
        elif kind == 'local_llm':
            if client is None:
                raise ValueError('runtime unavailable')
            raw = client.decide(evidence)
        else:
            raise ValueError('unknown router')
    except (ValueError, OSError, TimeoutError, KeyError, TypeError) as error:
        raw, failure = 'abstain', str(error)
    identity = model if kind == 'classifier' else {'kind': kind, 'sensitive_pattern': SENSITIVE.pattern}
    if kind == 'local_llm' and client:
        identity = {k: v for k, v in client.runtime.items() if k != 'setup_seconds'}
    return {'decision': 'escalate' if reasons else raw, 'raw_decision': raw,
            'reasons': reasons or ([failure] if failure else ['model_recommendation']), 'failure': failure,
            'input_fingerprint': digest(evidence),
            'model_fingerprint': digest(identity),
            'milliseconds': (time.monotonic()-started)*1000, 'advisory_only': True}


def metrics(rows, results):
    labeled = [(r, p) for r, p in zip(rows, results) if r['label'] is not None]
    times = sorted(r['milliseconds'] for r in results)
    return {'cases': len(rows), 'labeled': len(labeled), 'unresolved': len(rows)-len(labeled),
            'missed_escalations': sum(r['label'] and p['decision'] == 'retain_cheap_review' for r, p in labeled),
            'unnecessary_escalations': sum(not r['label'] and p['decision'] == 'escalate' for r, p in labeled),
            'raw_missed_escalations': sum(r['label'] and p['raw_decision'] == 'retain_cheap_review' for r, p in labeled),
            'abstentions': sum(p['decision'] == 'abstain' for p in results),
            'raw_abstentions': sum(p['raw_decision'] == 'abstain' for p in results),
            'failures': sum(p['failure'] is not None for p in results),
            'failure_reasons': dict(Counter(p['failure'] for p in results if p['failure'])),
            'median_ms': times[len(times)//2] if times else None,
            'p95_ms': times[max(0, math.ceil(len(times)*0.95)-1)] if times else None}
