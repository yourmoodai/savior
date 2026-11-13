"""Render review evidence without model-generated prose."""
import html

from bedrock_client import complete, BedrockError


def fallback_overview(report):
    return (
        f"This change set contains {report.get('files', 0)} changed files. The review completed "
        f"{report.get('completed_chunks', 0)} of {report.get('total_chunks', 0)} text chunks. "
        "A reliable combined description of the implementation is unavailable because the review "
        "or its final synthesis did not finish successfully. The details below distinguish "
        "reported code defects from review failures and identify any assets excluded from "
        "code review. Resolve those failures and rerun the review before relying on an approval."
    )


def finalize(report, *, qualified, synthesize=True):
    report['qualified'] = qualified
    failures = []
    if not qualified:
        failures.append('Reviewer qualification has not passed; inspect qualification results and any access errors before rerunning.')
    if not report.get('complete'):
        failures.append('Required review coverage is incomplete; resolve the failed chunks or unsupported binary changes and rerun.')
    if any(chunk.get('disposition') == 'COMMENT' for chunk in report.get('chunks', [])):
        failures.append('A chunk reviewer abstained; resolve its stated uncertainty and rerun.')
    if report.get('disposition') != 'APPROVE' and not report.get('findings') and not failures:
        failures.append('The assessment did not establish a clean review; inspect the chunk results and rerun.')
    report['reporting_mode'] = 'deterministic'
    report['reporting_complete'] = True
    report['synthesis_complete'] = False
    report['overview'] = (
        f"Review completed {report.get('completed_chunks', 0)} of {report.get('total_chunks', 0)} chunks "
        f"across {report.get('files', 0)} files; {len(report.get('findings', []))} verified findings. "
        "Full findings, reviewer judgments, and coverage gaps are retained in the report artifact."
    )
    if report.get('context_gaps'):
        failures.append('Supporting context was omitted; review remains incomplete.')
    report['review_failures'] = failures
    clean = qualified and report.get('complete') and report['reporting_complete'] and not failures and not report.get('findings') and report.get('disposition') == 'APPROVE'
    report['disposition'] = 'APPROVE' if clean else 'REQUEST_CHANGES'
    import os
    if os.environ.get('REVIEW_BUDGET_LEDGER'):
        from budget import Ledger
        report['budget'] = Ledger(os.environ['REVIEW_BUDGET_LEDGER']).summary()
    report['body'] = render(report)
    return report


def render(report):
    def prose(value, limit):
        value = html.escape(str(value)).replace('\\', '\\\\').replace('*', '\\*').replace('_', '\\_').replace('`', '\\`').replace('[', '\\[').replace('#', '\\#')
        encoded = value.encode('utf-16-le')
        return value if len(encoded) <= limit*2 else encoded[:(limit-1)*2].decode('utf-16-le', errors='ignore') + '…'
    verdict = 'Approved' if report['disposition'] == 'APPROVE' else 'Request changes'
    lines = [prose(report['overview'], 900), '', f'**Verdict: {verdict}**', '',
             f"Coverage: {report.get('completed_chunks', 0)}/{report.get('total_chunks', 0)} chunks."]
    if report.get('analysis_limitations'):
        lines.append(f"Runtime analysis: {len(report['analysis_limitations'])} disclosed uncertainties; source coverage does not prove runtime targets. See the artifact.")
    if report.get('budget'):
        budget = report['budget']
        if 'settled_usage_nanodollars' in budget and 'outstanding_reserved_nanodollars' in budget:
            lines.append(f"Usage-priced cost: ${budget['settled_usage_nanodollars']/1e9:.6f}; "
                         f"outstanding reservations: ${budget['outstanding_reserved_nanodollars']/1e9:.6f}. "
                         "Usage pricing is not invoice reconciliation.")
        else:
            lines.append(f"Inference committed (usage plus holds): ${budget['committed_usd']:.4f}.")
    findings = report.get('findings', [])
    if findings:
        lines += ['', 'Findings:']
        for finding in findings[:3]:
            lines.append('- ' + prose(finding['file'], 100) + ': ' + prose(finding['details'], 200))
        if len(findings) > 3:
            lines.append(f'{len(findings)-3} additional findings are in the artifact.')
    if report.get('review_failures'):
        lines += ['', f"Review incomplete: {len(report['review_failures'])} review failures; see artifact."]
    lines += ['', 'Full findings, chunk summaries, exclusions, and evidence: `report.json` in the **adversarial-report** artifact linked from the CI run above.']
    return '\n'.join(lines)
