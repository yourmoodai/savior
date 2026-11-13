import unittest
from experiment import audit_counterexample, grade, replay


class ExperimentTests(unittest.TestCase):
    def test_historical_incorrect_outputs_are_reproduced(self):
        self.assertTrue(replay()['all_known_counterexample_errors_detected'])

    def test_valid_counterexample_and_invalid_precondition(self):
        result = audit_counterexample(['abcdef', 3, 4, [(2, 5)], 'X'],
                                      {'raised': 'ValueError'}, {'returned': 'abcXef'})
        self.assertTrue(result['demonstrates_regression'])
        self.assertTrue(result['claimed_outputs_correct'])
        result = audit_counterexample(['abc', 1, 5, [(0, 3)], 'X'], {}, {})
        self.assertFalse(result['demonstrates_regression'])

    def test_flag_with_wrong_location_does_not_detect_defect(self):
        result = grade(expected_defect=True, complete=True, findings=[{}],
                       adjudications=[{'behavior_correct': True, 'location_correct': False}])
        self.assertTrue(result['case_flag_detected'])
        self.assertTrue(result['missed_defect'])

    def test_unknown_and_incomplete_results_are_excluded(self):
        for complete, adjudication in [(False, {'behavior_correct': True, 'location_correct': True}), (True, {})]:
            self.assertFalse(grade(expected_defect=True, complete=complete, findings=[{}],
                                   adjudications=[adjudication])['accuracy_eligible'])


if __name__ == '__main__':
    unittest.main()
