import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cost_calibration as calibration
import bedrock_client
from model_catalog import digest, routing_override
from test_live_packing import envelope


class CostCalibrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)/calibration.EXPERIMENT
        for context in (patch.dict(os.environ, AWS_PROFILE='awill', AWS_REGION='us-east-1', GITHUB_ACTIONS='false'),
                        patch.object(calibration, 'directory', return_value=self.root),
                        patch.object(calibration, 'aws', return_value={'Account': calibration.account(), 'Arn': 'synthetic'})):
            context.start()
            self.addCleanup(context.stop)
        with patch.object(bedrock_client, 'invoke', side_effect=AssertionError('freeze must be offline')):
            self.frozen = calibration.freeze('glm_4_7_flash')

    def test_offline_freeze_and_mocked_pass_preserve_cost_and_quality(self):
        self.assertFalse(self.root.exists())
        self.assertFalse(self.frozen['paid_authorized'])
        original_routing = routing_override.get()
        with patch.object(bedrock_client, 'invoke', return_value=envelope()) as invoke:
            result = calibration.run(self.frozen, self.frozen['fingerprint'], initialize=True)
            count = invoke.call_count
            again = calibration.run(self.frozen, self.frozen['fingerprint'])
            self.assertEqual(invoke.call_count, count)
        self.assertEqual(count, 36)
        self.assertEqual(result, again)
        self.assertEqual(result['matched_usage_priced_nanodollars'], count*15000)
        self.assertGreater(result['matched_reserved_nanodollars'], result['matched_request_estimated_nanodollars'])
        self.assertGreater(result['matched_request_estimated_nanodollars'], result['matched_usage_priced_nanodollars'])
        self.assertEqual(result['budget']['outstanding_nanodollars'], 0)
        self.assertEqual(result['complete_case_flag_misses'], 6)
        self.assertFalse(result['production_qualified'])
        self.assertEqual(routing_override.get(), original_routing)

    def test_approval_configuration_and_account_checks_precede_inference(self):
        with patch.object(bedrock_client, 'invoke') as invoke:
            with self.assertRaises(calibration.Stopped):
                calibration.run(self.frozen, 'wrong', initialize=True)
            self.assertFalse(self.root.exists())
            changed = copy.deepcopy(self.frozen)
            changed['execution_sources'] = {}
            changed['fingerprint'] = digest({k: v for k, v in changed.items() if k != 'fingerprint'})
            with self.assertRaises(calibration.Stopped):
                calibration.run(changed, changed['fingerprint'], initialize=True)
            with patch.object(calibration, 'aws', return_value={'Account': 'wrong'}):
                with self.assertRaises(calibration.Stopped):
                    calibration.run(self.frozen, self.frozen['fingerprint'], initialize=True)
            invoke.assert_not_called()

    def test_missing_history_never_recreates_ledger(self):
        ledger = calibration.budget.ResearchLedger.initialize(self.root, self.frozen)
        ledger.path.unlink()
        with patch.object(bedrock_client, 'invoke') as invoke:
            for initialize in (False, True):
                with self.assertRaises(calibration.budget.ResearchStopped):
                    calibration.run(self.frozen, self.frozen['fingerprint'], initialize=initialize)
            invoke.assert_not_called()

    def test_interruption_keeps_hold_and_blocks_further_calls(self):
        with patch.object(bedrock_client, 'invoke', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                calibration.run(self.frozen, self.frozen['fingerprint'], initialize=True)
        result = json.loads((self.root/'report.json').read_text())
        self.assertGreater(result['budget']['outstanding_nanodollars'], 0)
        self.assertEqual(result['matched_calls'], 0)
        self.assertEqual(result['unknown_usage'], 1)
        with patch.object(bedrock_client, 'invoke') as invoke:
            with self.assertRaises(calibration.Stopped):
                calibration.run(self.frozen, self.frozen['fingerprint'])
            invoke.assert_not_called()

    def test_missing_usage_keeps_hold_and_uncertain_finding_labels_stay_visible(self):
        with patch.object(bedrock_client, 'invoke', return_value=envelope(False)):
            with self.assertRaises(calibration.budget.ResearchStopped):
                calibration.run(self.frozen, self.frozen['fingerprint'], initialize=True)
        ledger = calibration.budget.ResearchLedger(self.root)
        self.assertGreater(ledger.summary()['outstanding_nanodollars'], 0)
        for role in calibration.FOCUS:
            ledger.append({'kind': 'calibration_result', 'case': self.frozen['cases'][0]['id'], 'role': role,
                           'result': {'disposition': 'REQUEST_CHANGES', 'findings': [{'file': 'x:1', 'details': 'unadjudicated'}], 'summary': 'defect'}})
        result = calibration.report(ledger, self.frozen)
        self.assertFalse(result['cases'][0]['grade']['accuracy_eligible'])
        self.assertGreater(result['cases'][0]['grade']['unresolved_findings'], 0)


if __name__ == '__main__':
    unittest.main()
