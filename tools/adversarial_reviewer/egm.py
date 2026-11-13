"""Independent evidence reviews with lossless deterministic reconciliation."""
import json
from model_catalog import select
from diagnostics import record

FOCUS = {
    'correctness': 'Prioritize correctness, failure paths and repository invariants.',
    'security': 'Prioritize privacy, authorization, trust boundaries and untrusted input.',
    'compatibility': 'Prioritize compatibility, regression coverage and observable behavior.',
}


def review(assess, diff, files, docs, anchors, *, deadline, supporting_context):
    findings, summaries, failures = [], [], []
    for role, focus in FOCUS.items():
        # Each request is rebuilt from source evidence, never sibling conclusions.
        with select(role):
            result = assess(diff, files, docs, anchors, deadline=deadline,
                            supporting_context=supporting_context+'\nReviewer focus: '+focus)
        record(stage='goldfish', role=role, disposition=result[0], findings=result[1], summary=result[2])
        if result[0] == 'COMMENT' or result[2].startswith('review incomplete:'):
            failures.append(role+': '+result[2])
        findings.extend(result[1])
        summaries.append({'role': role, 'summary': result[2]})
    # Exact duplicates only: two failures at one location may have different causes.
    unique = {json.dumps(f, sort_keys=True): f for f in findings}
    findings = list(unique.values())
    summary = '\n'.join(item['role']+': '+item['summary'] for item in summaries)
    if failures:
        summary = 'review incomplete: '+summary
    record(stage='deterministic_reconciliation', findings=findings, reviews=summaries, failures=failures)
    return ('COMMENT' if failures else 'REQUEST_CHANGES' if findings else 'APPROVE'), findings, summary
