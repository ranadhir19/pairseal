import unittest

from pairseal.selftest import render_report, run_selftest


class SelftestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_selftest()

    def test_all_fixtures_and_controls_pass(self):
        self.assertEqual(self.report["problems"], [])
        self.assertEqual(self.report["passed"],
                         self.report["total"])

    def test_check_mismatch_fixture_present_and_passing(self):
        by_id = {r["id"]: r for r in self.report["results"]}
        self.assertIn("check_mismatch", by_id)
        self.assertTrue(by_id["check_mismatch"]["passed"])

    def test_report_renders_deterministically(self):
        self.assertEqual(render_report(self.report),
                         render_report(run_selftest()))


if __name__ == "__main__":
    unittest.main()
