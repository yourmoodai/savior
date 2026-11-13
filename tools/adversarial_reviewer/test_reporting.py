import unittest
import json
from unittest.mock import patch
from reporting import finalize, fallback_overview
from bedrock_client import BedrockError

OVERVIEW = ('This change updates the review pipeline to assess independent code chunks and combine their results into a concise overview. '
            'The publisher derives approval from complete coverage and verified findings, while retaining individual results for inspection. '
            'Documentation images are identified separately from executable changes. Review failures remain visible and require a successful rerun before approval can be granted.')


class ReportingTests(unittest.TestCase):
    def report(self, **changes):
        result=dict(files=2, complete=True, disposition='APPROVE', findings=[], total_chunks=2, completed_chunks=2,
                    chunks=[dict(index=n, file=f'file{n}', complete=True, disposition='APPROVE', summary=f'behavior{n}') for n in [1,2]],
                    excluded_assets=['docs/assets/savior.png'], summary='both chunks reviewed')
        result.update(changes)
        return result

    def test_complete_review_approves_without_prose_inference(self):
        with patch('reporting.complete', side_effect=AssertionError('paid prose forbidden')):
            report = finalize(self.report(), qualified=True)
        self.assertEqual(report['disposition'], 'APPROVE')
        self.assertTrue(report['reporting_complete'])
        self.assertFalse(report['synthesis_complete'])
        self.assertEqual(len(report['chunks']), 2)

    def test_findings_gaps_abstentions_and_qualification_block_approval(self):
        cases = [dict(complete=False), dict(disposition='COMMENT'),
                 dict(context_gaps=[{'chunk': 1, 'omitted_support': ['helper']}]),
                 dict(findings=[dict(file='x:1', details='verified boundary defect')]),
                 dict(chunks=[dict(index=1, disposition='COMMENT', summary='unresolved input contract')])]
        for changes in cases:
            original = self.report(**changes)
            result = finalize(original, qualified=True)
            self.assertEqual(result['disposition'], 'REQUEST_CHANGES')
            for field in ('chunks', 'findings', 'context_gaps'):
                if field in changes:
                    self.assertEqual(result[field], changes[field])
        self.assertEqual(finalize(self.report(), qualified=False)['disposition'], 'REQUEST_CHANGES')

    def test_arbitrarily_large_summaries_do_not_require_paid_synthesis(self):
        chunks = [dict(index=n, file=f'file{n}', complete=True, disposition='APPROVE',
                       summary='preserve this judgment ' * 300) for n in range(88)]
        with patch('reporting.complete', side_effect=AssertionError('paid prose forbidden')):
            report = finalize(self.report(chunks=chunks, total_chunks=88, completed_chunks=88), qualified=True)
        self.assertEqual(report['chunks'], chunks)
        self.assertEqual(report['disposition'], 'APPROVE')

    def test_large_report_preserves_every_chunk_and_full_evidence(self):
        from reporting import render
        chunks = [dict(index=n, file=f'file{n}.rs', complete=True,
                       summary='behavior ' * 70) for n in range(172)]
        report = self.report(chunks=chunks, overview=OVERVIEW)
        body = render(report)
        self.assertLess(len(body.encode('utf-16-le')) // 2, 2300)
        for chunk in chunks:
            self.assertNotIn(f"### Chunk {chunk['index']} —", body)
            self.assertEqual(chunk['summary'], 'behavior ' * 70)
        self.assertIn('adversarial-report', body)

    def test_comment_is_bounded_with_unicode_and_many_findings(self):
        from reporting import render
        findings = [dict(file='🧪'*500, details='🧪'*2000) for _ in range(12)]
        report = self.report(overview=OVERVIEW, findings=findings, disposition='REQUEST_CHANGES')
        body = render(report)
        self.assertLessEqual(len(body.encode('utf-16-le'))//2, 2300)
        self.assertIn('9 additional findings', body)
        self.assertEqual(len(report['findings']), 12)
