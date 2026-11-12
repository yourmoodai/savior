"""Lossless candidate reports and matched-case research measurements."""
import html
import json

from model_catalog import digest

ARMS = ('current', 'three_role', 'single_role')


def escape(value):
    text = html.escape(str(value))
    for character in ('\\', '`', '*', '_', '[', ']', '#', '|', '<', '>'):
        text = text.replace(character, '\\' + character)
    return text.replace('\n', '<br>')


def deterministic(assessments, required_roles, interruption=None):
    findings, unresolved = {}, []
    completed = []
    for role in required_roles:
        item = assessments.get(role)
        if item is None:
            unresolved.append({'role': role, 'reason': 'assessment missing'})
            continue
        for finding in item['findings']:
            findings[digest(finding)] = finding
        if item['disposition'] == 'COMMENT' or item['summary'].startswith('review incomplete:'):
            unresolved.append({'role': role, 'reason': item['summary']})
        else:
            completed.append(role)
    if interruption:
        unresolved.append({'role': 'execution', 'reason': interruption})
    result = {'findings': list(findings.values()), 'unresolved': unresolved,
              'coverage': {'required_roles': list(required_roles), 'completed_roles': completed},
              'complete': not unresolved, 'assessments': assessments}
    result['disposition'] = 'REQUEST_CHANGES' if findings else 'COMMENT' if unresolved else 'APPROVE'
    lines = [f"Disposition: {result['disposition']}", '',
             'Coverage: ' + escape(json.dumps(result['coverage'], sort_keys=True)), '', 'Findings:']
    lines.extend('- ' + escape(json.dumps(f, sort_keys=True)) for f in result['findings'])
    lines += ['', 'Unresolved judgments:']
    lines.extend('- ' + escape(json.dumps(item, sort_keys=True)) for item in unresolved)
    lines += ['', 'Assessment outcomes:']
    lines.extend('- ' + escape(json.dumps({'role': role, **item}, sort_keys=True))
                 for role, item in assessments.items())
    result['body'] = '\n'.join(lines)
    return result


def lossless(report, assessments, required_roles, interruption=None):
    expected = deterministic(assessments, required_roles, interruption)
    return all(report.get(key) == expected[key] for key in expected)


def collect(frozen, events):
    rows = []
    for case in frozen['cases']:
        assessments = {'current': {}, 'single_role': {}}
        results, started = {}, set()
        for event in events:
            if event.get('case') != case['id']:
                continue
            arm = event.get('arm')
            if event['kind'] == 'step_started':
                started.add(arm)
            elif event['kind'] == 'role_result':
                assessments[arm][event['role']] = event['result']
            elif event['kind'] == 'step_result':
                results[arm] = event['result']
        interrupted = started - results.keys()
        interruption = 'execution interrupted before arm completed'
        for arm in interrupted:
            required = frozen['roles'] if arm == 'current' else ['general']
            results[arm] = deterministic(assessments[arm], required, interruption)
        if 'current' in started:
            results['three_role'] = deterministic(assessments['current'], frozen['roles'])
            results['three_role']['lossless'] = lossless(results['three_role'], assessments['current'], frozen['roles'])
        if 'single_role' in results:
            result = results['single_role']
            result['lossless'] = lossless(result, assessments['single_role'], ['general'],
                                          interruption if 'single_role' in interrupted else None)
        rows.append({'case': case['id'], 'expected': case['expected'], 'oracle': case['oracle'], 'arms': results})
    return rows


def accuracy(rows, arm):
    labeled = [r for r in rows if r['oracle']['resolved'] and arm in r['arms']]
    return {
        'assessed_cases': len(labeled),
        'complete_cases': sum(r['arms'][arm]['complete'] for r in labeled),
        'false_positive_cases': [r['case'] for r in labeled
                                 if r['expected'] == 'APPROVE' and r['arms'][arm]['findings']],
        'missed_defect_cases': [r['case'] for r in labeled if r['expected'] == 'REQUEST_CHANGES'
                                and r['arms'][arm]['complete'] and not r['arms'][arm]['findings']],
        'unresolved_defect_cases': [r['case'] for r in labeled if r['expected'] == 'REQUEST_CHANGES'
                                    and not r['arms'][arm]['complete'] and not r['arms'][arm]['findings']],
        'incomplete_cases': [r['case'] for r in labeled if not r['arms'][arm]['complete']],
    }


def report(frozen, ledger):
    events = ledger.events()
    rows = collect(frozen, events)
    matched = [r for r in rows if set(r['arms']) == set(ARMS) and r['oracle']['resolved']]
    pairwise = {}
    for candidate in ARMS[1:]:
        paired = [r for r in rows if r['oracle']['resolved'] and all(a in r['arms'] for a in ('current', candidate))]
        pairwise[candidate] = {arm: accuracy(paired, arm) for arm in ('current', candidate)}
    summary = ledger.summary()
    metrics = {}
    for arm in ('current', 'single_role'):
        calls = [c for c in summary['calls'] if c['context'].get('arm') == arm]
        trace = [e for e in events if e['kind'] == 'diagnostics' and e.get('arm') == arm]
        diagnostics = [d for e in trace for d in e['events']]
        failures = [d.get('validation_error', '') for d in diagnostics if d.get('validation_error')]
        invalid_summaries = 0
        for event in events:
            if (event['kind'] != 'transport_response' or event['context'].get('arm') != arm
                    or event['context'].get('role') != 'elephant' or event['response'].get('stopReason') != 'end_turn'):
                continue
            try:
                value = json.loads(''.join(b['text'] for b in event['response']['output']['message']['content']))
            except (ValueError, KeyError, TypeError):
                continue  # Invalid JSON is already counted by completion diagnostics.
            field = 'overview' if event['context']['purpose'] == 'overview' else 'summary'
            valid = isinstance(value, dict) and set(value) == {field} and isinstance(value[field], str)
            if valid:
                valid = 50 <= len(value[field].split()) <= 100 if field == 'overview' else 10 <= len(value[field]) <= 1200
            invalid_summaries += not valid
        metrics[arm] = {
            'reservations': len(calls),
            'invocation_attempts': sum(e['kind'] == 'transport_started' and e['context'].get('arm') == arm
                                       for e in events if e['kind'] == 'transport_started'),
            'charged_nanodollars': sum(c['charged'] or 0 for c in calls),
            'outstanding_nanodollars': sum(c['reserved'] for c in calls if c['charged'] is None),
            'input_tokens': sum((c['metrics'] or {}).get('usage', {}).get('inputTokens', 0) for c in calls),
            'output_tokens': sum((c['metrics'] or {}).get('usage', {}).get('outputTokens', 0) for c in calls),
            'unknown_usage_calls': sum(c['metrics'] is None for c in calls),
            'latency_seconds': sum(e['seconds'] for e in events if e['kind'] in ('transport_response', 'transport_failed')
                                    and e['context'].get('arm') == arm),
            'abstentions': sum('model abstained:' in message for message in failures),
            'invalid_responses': sum(d['stage'] == 'invalid_answer' for d in diagnostics)
                                 + sum('model abstained:' not in message for message in failures) + invalid_summaries,
            'truncations': sum(e['response'].get('stopReason') == 'max_tokens' for e in events
                               if e['kind'] == 'transport_response' and e['context'].get('arm') == arm),
        }
    shared = [c for c in summary['calls'] if c['context'].get('arm') == 'current' and c['role'] != 'elephant']
    metrics['three_role'] = {'incremental_calls': 0, 'incremental_charge_nanodollars': 0,
                             'shared_assessment_calls': len(shared),
                             'shared_charged_nanodollars': sum(c['charged'] or 0 for c in shared),
                             'shared_outstanding_nanodollars': sum(c['reserved'] for c in shared if c['charged'] is None),
                             'assessment_cost_shared_with': 'current'}
    extra_misses = [r['case'] for r in matched if r['expected'] == 'REQUEST_CHANGES'
                    and r['arms']['current']['findings'] and not r['arms']['single_role']['findings']]
    losses = [r['case'] for r in rows for arm in ARMS[1:]
              if arm in r['arms'] and not r['arms'][arm].get('lossless', False)]
    return {'schema': 1, 'experiment': frozen['experiment'], 'frozen_digest': digest(frozen),
            'budget': summary, 'cases': rows, 'matched_cases': [r['case'] for r in matched],
            'matched_accuracy': {arm: accuracy(matched, arm) for arm in ARMS}, 'pairwise_accuracy': pairwise,
            'excluded_unresolved_labels': [r['case'] for r in rows if not r['oracle']['resolved']],
            'metrics': metrics, 'additional_single_role_misses': extra_misses,
            'reporting_losses': losses, 'production_qualification': 'not run; no cache publication',
            'status': next((e['reason'] for e in reversed(events) if e['kind'] == 'stopped'), 'not started'),
            'packing': frozen['packing']}


def markdown(result):
    budget = result['budget']
    lines = ['# Reviewer design research', '',
             'This synthetic trial compares assessment and reporting behavior. It does not qualify a production reviewer.', '',
             f"Status: {escape(result['status'])}.", '',
             f"Usage-priced charges: ${budget['charged_nanodollars'] / 1e9:.6f}. "
             f"Outstanding reservations: ${budget['outstanding_nanodollars'] / 1e9:.6f}. "
             f"Remaining allowance: ${budget['remaining_nanodollars'] / 1e9:.6f} of $4.", '',
             'Charges use reported tokens at frozen catalog prices; they are not an AWS billing reconciliation.', '',
             f"Matched cases: {len(result['matched_cases'])} of {len(result['cases'])}. "
             'Only matched cases with resolved labels enter the accuracy table. Incomplete reviews are reported separately.', '',
             '| Arm | Complete | False positives | Missed defects | Incomplete |',
             '| --- | ---: | ---: | ---: | ---: |']
    for arm, metric in result['matched_accuracy'].items():
        lines.append(f"| {arm} | {metric['complete_cases']} | {len(metric['false_positive_cases'])} | "
                     f"{len(metric['missed_defect_cases'])} | {len(metric['incomplete_cases'])} |")
    lines += ['', '## Calls and usage', '', '| Arm | Calls | Input tokens | Output tokens | Seconds | Abstentions | Invalid | Truncated |',
              '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for arm in ('current', 'single_role'):
        m = result['metrics'][arm]
        lines.append(f"| {arm} | {m['invocation_attempts']} | {m['input_tokens']} | {m['output_tokens']} | "
                     f"{m['latency_seconds']:.2f} | {m['abstentions']} | {m['invalid_responses']} | {m['truncations']} |")
    lines += ['', 'The three-role candidate reuses current-arm assessments and verification. Its incremental reporting cost is zero; '
              'the shared assessment cost is not a second charge.', '', '## Case outcomes', '',
              '| Case | Label | Current | Three roles | Single role |', '| --- | --- | --- | --- | --- |']
    for row in result['cases']:
        cells = []
        for arm in ARMS:
            item = row['arms'].get(arm)
            cells.append('not run' if item is None else f"{item['disposition']}; {'complete' if item['complete'] else 'incomplete'}")
        label = row['expected'] if row['oracle']['resolved'] else 'unresolved; excluded'
        lines.append('| ' + ' | '.join([escape(row['case']), label, *cells]) + ' |')
    lines += ['', '## Follow-up ranking', '']
    if result['reporting_losses']:
        lines.append('1. Disqualify deterministic reporting until all observed losses are repaired: '
                     + escape(', '.join(result['reporting_losses'])) + '.')
    else:
        lines.append('1. Review deterministic reporting as a separate production proposal. Lossless rendering tests establish the mechanical contract; '
                     'this trial alone cannot establish coverage on real PRs.')
    lines.append('2. Bind future quotes to the cheapest qualified packing plan with equivalent primary evidence coverage, frozen prices, '
                 'routing, prompts, base/head revisions and retry bounds. Revalidate the exact plan at execution.')
    if result['additional_single_role_misses']:
        lines.append('3. Do not recommend the single-role candidate: it lost detected defects on matched cases: '
                     + escape(', '.join(result['additional_single_role_misses'])) + '.')
    else:
        lines.append('3. Keep single-role review exploratory. A small synthetic sample, even with no additional miss, cannot establish equivalence.')
    lines += ['', '## Limits', '',
              'Labels come from bounded executable probes, not exhaustive proofs or independent adjudication. '
              'Case-level false positives count benign cases with findings; defect matching does not independently adjudicate each finding. '
              'Correlated roles use the same model. Early stopping can bias matched cases. '
              'Runtime qualification is deliberately bypassed for this research comparison and no approval is published. '
              'Missing usage and interrupted calls retain their full reservations.', '',
              'The accompanying `report.json`, frozen manifest and append-only ledger contain complete findings, coverage gaps, '
              'unresolved judgments, call responses, per-arm accounting and pairwise comparisons.', '']
    return '\n'.join(lines)
