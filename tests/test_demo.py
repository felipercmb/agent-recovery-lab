"""Verify the public command, including its intentionally failing example."""

import json
import subprocess
import sys
import unittest


class DemoTests(unittest.TestCase):
    def invoke(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "agent_recovery.demo", *args],
            text=True, capture_output=True, check=False, timeout=10,
        )

    def test_safe_strategy_passes_all_five_scenarios(self):
        result = self.invoke("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["passed"])
        self.assertEqual(len(data["reports"]), 5)
        by_name = {report["scenario"]: report for report in data["reports"]}
        self.assertFalse(by_name["outage"]["completed"])
        self.assertEqual(by_name["outage"]["provider_attempts"], 3)
        self.assertEqual(len(by_name["after-commit"]["effects"]), 1)
        self.assertEqual(by_name["after-commit"]["effect_attempts"], 2)

    def test_unsafe_strategy_exits_one_with_duplicate_diagnostic(self):
        result = self.invoke("--strategy", "unsafe", "--scenario", "after-commit", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data["passed"])
        report = data["reports"][0]
        self.assertEqual(len(report["effects"]), 2)
        self.assertIn("duplicate_effect", [v["code"] for v in report["violations"]])

    def test_json_is_reproducible(self):
        first, second = self.invoke("--json"), self.invoke("--json")
        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stdout, second.stdout)

    def test_invalid_strategy_is_usage_error(self):
        result = self.invoke("--strategy", "typo")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)

    def test_text_report_explains_failure(self):
        result = self.invoke("--strategy", "unsafe", "--scenario", "after-commit")
        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicate_effect", result.stdout)
        self.assertIn("committed_effects=2", result.stdout)
        self.assertIn("effect_attempts=2", result.stdout)

    def test_text_report_distinguishes_write_attempts_from_commits(self):
        result = self.invoke("--scenario", "after-commit")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("effect_attempts=2", result.stdout)
        self.assertIn("committed_effects=1", result.stdout)


if __name__ == "__main__":
    unittest.main()
