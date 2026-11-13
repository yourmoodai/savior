"""Reviewer experiments; paid calibration requires explicit manual execution."""
import argparse
import hashlib
import json
from pathlib import Path

from qualify_packing import freeze, summary
from adversarial_review import review_messages
from bedrock_client import estimate_input_units
from model_catalog import digest

ROOT = Path(__file__).resolve().parents[2]


def protected_outcome(text, start, end, protected, marker, *, defective):
    overlaps = any(start <= a and b <= end if defective else start < b and a < end
                   for a, b in protected)
    return {'raised': 'ValueError'} if overlaps else {'returned': text[:start] + marker + text[end:]}


def audit_counterexample(inputs, claimed_old, claimed_new):
    text, start, end, protected, marker = inputs
    valid = 0 <= start < end <= len(text) and all(0 <= a < b <= len(text) for a, b in protected)
    old = protected_outcome(*inputs, defective=False)
    new = protected_outcome(*inputs, defective=True)
    return {'preconditions_valid': valid, 'old': old, 'new': new,
            'demonstrates_regression': valid and old != new,
            'claimed_outputs_correct': valid and old == claimed_old and new == claimed_new}


def grade(*, expected_defect, complete, findings, adjudications):
    """Unknown finding truth never becomes a successful negative label."""
    if len(findings) != len(adjudications):
        raise ValueError('one adjudication is required per finding')
    resolved = all(a.get('behavior_correct') in (True, False) and a.get('location_correct') in (True, False)
                   for a in adjudications)
    correct = sum(a.get('behavior_correct') is True and a.get('location_correct') is True for a in adjudications)
    return {'case_flag_detected': bool(findings), 'complete': complete,
            'finding_labels_resolved': resolved, 'accuracy_eligible': complete and resolved and expected_defect is not None,
            'correct_findings': correct,
            'missed_defect': expected_defect is True and complete and resolved and correct == 0,
            'false_positive': expected_defect is False and complete and resolved and bool(findings),
            'unresolved_findings': sum(a.get('behavior_correct') is None or a.get('location_correct') is None for a in adjudications)}


def replay():
    files = ['reviewer-designs-results.json', 'reviewer-designs-counterexamples.json',
             'packing-live-results.json', 'cpu-review-routing-results.json', 'packing-quality-results.json']
    preserved = {name: hashlib.sha256((ROOT/'docs/research'/name).read_bytes()).hexdigest() for name in files}
    checks = [
        audit_counterexample(['abcde', 1, 4, [(2, 3)], 'X'], {'raised': 'ValueError'}, {'returned': 'aXde'}),
        audit_counterexample(['abcdef', 3, 4, [(2, 5)], 'X'], {'raised': 'ValueError'}, {'returned': 'abXef'}),
        audit_counterexample(['abcdef', 3, 6, [(2, 5)], 'X'], {'raised': 'ValueError'}, {'returned': 'abXf'}),
        audit_counterexample(['abcdefghij', 7, 12, [(5, 10)], 'X'], {'raised': 'ValueError'}, {'returned': 'abcdefX'}),
    ]
    historical = json.loads((ROOT/'docs/research/packing-live-results.json').read_text())
    graded = []
    for case in historical['cases']:
        arms = {}
        for name, arm in case['arms'].items():
            adjudications = [{} for _ in arm['findings']]
            if case['id'] == 'json_records_defective' and name == 'baseline':
                adjudications = [{'behavior_correct': True, 'location_correct': False} for _ in arm['findings']]
            arms[name] = grade(expected_defect=case['expected'] == 'REQUEST_CHANGES',
                               complete=arm['complete'], findings=arm['findings'], adjudications=adjudications)
        graded.append({'case': case['id'], 'arms': arms})
    return {'schema': 1, 'paid_authorized': False, 'production_qualified': False,
            'historical_artifact_sha256': preserved, 'historical_budget': historical['budget'],
            'protected_span_counterexamples': checks,
            'finding_grades': graded,
            'location_audit': historical['json_location_audit'],
            'interpretation': 'trusted fixture replay; no model inference; historic results are not results of the repaired harness',
            'all_known_counterexample_errors_detected': all(not c['claimed_outputs_correct'] for c in checks)}


def freeze_next():
    protocol = freeze()
    protocol.update(schema=2, experiment='review-harness-v2', paid_authorized=False)
    for row in protocol['rows']:
        row['split'] = 'held_out' if row['case'].get('family') in ('protocol_compatibility', 'helper_last') else 'development'
        for arm in row['arms'].values():
            # Use the maximum escaped JSON size allowed by the finding schema.
            addition = '\nCandidate findings to verify: ' + json.dumps([
                {'location': 999999, 'invariant': '\U0001f9ea'*600,
                 'scenario': '\U0001f9ea'*600, 'correction': '\U0001f9ea'*600}])
            from adversarial_review import VERIFICATION_PROMPT
            sizes = []
            for request in arm['assessment_requests']:
                messages = [dict(m) for m in request['messages']]
                messages[-1]['content'] += VERIFICATION_PROMPT + addition
                sizes.append(estimate_input_units(messages))
            arm['verification_envelopes_fit'] = all(n <= 48000 for n in sizes)
            arm['maximum_verification_input_units'] = max(sizes, default=0)
        row['live_eligible'] &= all(a['verification_envelopes_fit'] for a in row['arms'].values())
    protocol['fingerprint'] = digest({k: v for k, v in protocol.items() if k != 'fingerprint'})
    return protocol


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('replay', 'freeze', 'cost-freeze', 'cost-run', 'cost-report', 'context-audit'))
    parser.add_argument('--output', type=Path, default=Path('.cache/review-experiment/report.json'))
    parser.add_argument('--candidate', default='glm_4_7_flash')
    parser.add_argument('--input', type=Path)
    parser.add_argument('--approve')
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--base', default='origin/main')
    parser.add_argument('--head', default='HEAD')
    parser.add_argument('--test-evidence', type=Path)
    args = parser.parse_args()
    if args.command == 'context-audit':
        from context_audit import audit
        result = audit(args.base, args.head, json.loads(args.test_evidence.read_text()) if args.test_evidence else None)
    elif args.command.startswith('cost-'):
        import cost_calibration as calibration
        if args.command == 'cost-freeze':
            result = calibration.freeze(args.candidate)
        elif args.command == 'cost-run':
            if not args.input or not args.approve:
                parser.error('cost-run requires --input and --approve <frozen-fingerprint>; authorizes at most $1')
            result = calibration.run(json.loads(args.input.read_text()), args.approve, initialize=args.initialize)
        else:
            ledger = calibration.budget.ResearchLedger(calibration.directory())
            result = calibration.report(ledger, json.loads((ledger.root/'frozen.json').read_text()))
    else:
        result = replay() if args.command == 'replay' else freeze_next()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    display = summary(result) if args.command == 'freeze' else result
    if args.command.startswith('cost-'):
        display = {k: v for k, v in result.items() if k not in ('execution_sources', 'cases', 'assessment_prompts', 'calls')}
    print(json.dumps(display, indent=2))


if __name__ == '__main__':
    main()
