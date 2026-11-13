"""Freeze paired packing evidence without authorizing or performing paid calls."""
import argparse
from collections import Counter
import json
from pathlib import Path

from packing import anchors, candidate, count_tokens, digest, manifest, messages, parse, plan, render
from adversarial_review import review_messages
from egm import FOCUS
from model_catalog import manifest as routing_manifest
from quote import estimate
from research_cases import cases


def addition(source, path):
    return f'diff --git a/{path} b/{path}\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(source.splitlines())} @@\n'+''.join('+'+line+'\n' for line in source.splitlines())


def required_ids(diff, markers):
    evidence = anchors(parse(diff))
    groups = []
    for group in markers:
        ids = []
        for text in group:
            matches = [a['id'] for a in evidence if a['evidence'].strip() == text.strip()]
            if len(matches) != 1:
                raise ValueError('ambiguous required context marker')
            ids += matches
        groups.append(ids)
    return groups


def fixtures():
    result = []
    for case in cases():
        result.append({**case, 'required_context': [[a['id'] for a in anchors(parse(case['diff']))]],
                       'expected_context_available': True})
    filler = ''.join(f'def filler_{i}():\n    return "'+('x'*1000)+'"\n\n' for i in range(75))
    helper = 'def allowed(current, saved):\n    return current and saved\n\n'
    for placement in ('helper_first', 'helper_last'):
        for defective in (False, True):
            condition = 'saved' if defective else 'allowed(current, saved)'
            caller = f'def retain(current, saved):\n    if {condition}:\n        return "persist"\n    return "discard"\n\n'
            source = helper+filler+caller if placement == 'helper_first' else caller+filler+helper
            namespace = {}
            exec(compile(source, '<trusted-packing-fixture>', 'exec'), namespace)
            passed = all(namespace['retain'](a, b) == ('persist' if a and b else 'discard')
                         for a in (False, True) for b in (False, True))
            diff = addition(source, 'synthetic/module.py')
            result.append({'id': placement+('_defective' if defective else '_benign'), 'family': placement,
                'diff': diff, 'context': 'Persistence requires current and saved consent. All functions are newly added.',
                'expected': 'REQUEST_CHANGES' if defective else 'APPROVE',
                'oracle': {'resolved': passed == (not defective), 'new_passed': passed},
                'required_context': required_ids(diff, [['return current and saved', f'if {condition}:']]),
                'expected_context_available': True})
    for defective in (False, True):
        records = [{'record_id': i, 'used': (5 if defective else 3) if i == 37 else 0,
                    'cap': 4, 'padding': 'x'*1000} for i in range(80)]
        source = json.dumps(records, indent=2)+'\n'
        passed = all(r['used'] <= r['cap'] for r in json.loads(source))
        diff = addition(source, 'synthetic/records.json')
        target = next(i for i, a in enumerate(anchors(parse(diff))) if a['evidence'].strip() == '"record_id": 37,')
        evidence = anchors(parse(diff))
        result.append({'id': 'json_records'+('_defective' if defective else '_benign'), 'family': 'json_records',
            'diff': diff, 'context': 'Each newly added record must have used <= cap.',
            'expected': 'REQUEST_CHANGES' if defective else 'APPROVE',
            'oracle': {'resolved': passed == (not defective), 'new_passed': passed},
            'required_context': [[a['id'] for a in evidence[target:target+3]]], 'expected_context_available': True})
    source = 'def oversized():\n'+''.join('    # '+('x'*1000)+'\n' for _ in range(80))+'    return True\n'
    diff = addition(source, 'synthetic/oversized.py')
    result.append({'id': 'oversized_context_control', 'family': 'capacity_control', 'diff': diff,
        'context': 'This control requires the complete function in one request.', 'expected': None,
        'oracle': {'resolved': False, 'reason': 'capacity control, not an accuracy label'},
        'required_context': [[a['id'] for a in anchors(parse(diff))]], 'expected_context_available': False})
    return result


def context_available(packed, required):
    views = []
    for chunk in packed['chunks']:
        ids = {a['id'] for a in anchors(chunk['units'])}
        # Candidate retains exact support ownership. Production baseline does not;
        # recover its unsplit original units by their rendered presence below.
        ids.update(chunk.get('support_evidence_ids', []))
        views.append(ids)
    return [any(set(group) <= view for view in views) for group in required]


def assess(case, strategy):
    packed = candidate(case['diff']) if strategy == 'semantic_bytes' else plan(case['diff'], count_tokens, messages, strategy='baseline')
    original = parse(case['diff'])
    if strategy == 'baseline':
        for chunk in packed['chunks']:
            chunk['support_evidence_ids'] = [a['id'] for unit in original if unit.render() in chunk['support'] for a in anchors([unit])]
    expected = Counter(a['id'] for a in anchors(original))
    actual = Counter(a['id'] for chunk in packed['chunks'] for a in anchors(chunk['units']))
    available = context_available(packed, case['required_context'])
    requests = []
    for index, chunk in enumerate(packed['chunks']):
        for role, focus in FOCUS.items():
            prompt = review_messages(render(chunk['units']), sorted({u.path for u in chunk['units']}),
                case['context'], anchors(chunk['units']),
                supporting_context='\nSupporting evidence (context only):\n'+chunk['support']+'\nReviewer focus: '+focus)
            requests.append({'chunk': index, 'role': role, 'messages': prompt,
                             'input_byte_estimate': count_tokens(prompt), 'prompt_fingerprint': digest(prompt)})
    described = manifest(packed)
    return {'coverage_complete': actual == expected and all(n == 1 for n in actual.values()),
            'required_context_available': available, 'chunks': len(packed['chunks']),
            'context_gaps': [c['omitted_support'] for c in packed['chunks']],
            'requests_fit': all(r['input_byte_estimate'] <= 48000 for r in requests),
            'reservation_nanodollars': sum(i['reserved_nanodollars'] for i in estimate(described)),
            'assessment_requests': requests}


def freeze():
    rows = []
    for index, case in enumerate(fixtures()):
        arms = {strategy: assess(case, strategy) for strategy in ('baseline', 'semantic_bytes')}
        passed = all(a['coverage_complete'] and a['requests_fit'] for a in arms.values())
        expected_available = case['expected_context_available']
        # Baseline is the comparator, not the acceptance target: record its losses
        # without rejecting an improvement that restores the required context.
        passed = passed and all(arms['semantic_bytes']['required_context_available']) == expected_available
        if not expected_available:
            passed = passed and not all(arms['baseline']['required_context_available'])
        live_eligible = passed and expected_available and case['oracle']['resolved']
        rows.append({'case': case, 'arms': arms, 'offline_passed': passed, 'live_eligible': live_eligible,
                     'order': ['baseline', 'semantic_bytes'] if index % 2 == 0 else ['semantic_bytes', 'baseline']})
    root = Path(__file__).resolve().parent
    sources = {str(p.relative_to(root.parent)): p.read_text() for p in sorted(root.glob('*.py'))}
    sources.update({str(p.relative_to(root.parent)): p.read_text() for p in sorted((root.parent/'adversarial_reviewer').glob('*.py'))})
    protocol = {'schema': 1, 'experiment': 'packing-quality-v1', 'paid_authorized': False,
        'production_qualified': False, 'routing': routing_manifest(), 'source_fingerprint': digest(sources),
        'temperature': 0, 'assessment_output_limit': 1024, 'verification_output_limit': 1536,
        'retry_output_limit': 4096, 'rows': rows,
        'live_requirements': ['separate authorized cap and durable ledger', 'verify configured AWS account and region',
            'reuse production evidence validation and finding verification', 'no assessment cache reuse',
            'full-context reservations before every call; uncertain usage remains reserved',
            'matched-case comparisons only; unresolved labels excluded',
            'any additional missed defect or false positive blocks recommendation',
            'compare findings semantically against executable or adjudicated evidence; no automatic promotion']}
    return {**protocol, 'fingerprint': digest(protocol)}


def summary(frozen):
    return {'fingerprint': frozen['fingerprint'], 'source_fingerprint': frozen['source_fingerprint'],
            'offline_passed': all(r['offline_passed'] for r in frozen['rows']),
            'paid_authorized': False, 'production_qualified': False,
            'cases': [{'id': r['case']['id'], 'offline_passed': r['offline_passed'], 'live_eligible': r['live_eligible'],
                       'arms': {s: {k: v for k, v in a.items() if k not in ('assessment_requests', 'context_gaps')}
                                for s, a in r['arms'].items()}} for r in frozen['rows']]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('.cache/packing-quality'))
    args = parser.parse_args()
    result = freeze()
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output/'frozen.json'
    if target.exists() and json.loads(target.read_text()) != result:
        raise ValueError('frozen protocol changed; use a new output directory')
    target.write_text(json.dumps(result, sort_keys=True, indent=2)+'\n')
    report = summary(result)
    (args.output/'summary.json').write_text(json.dumps(report, sort_keys=True, indent=2)+'\n')
    print(json.dumps({'cases': len(result['rows']), 'offline_passed': report['offline_passed'], 'paid_calls': 0}))
    if not report['offline_passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
