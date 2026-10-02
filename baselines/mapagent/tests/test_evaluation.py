"""Regression tests for strict multiple-choice answer scoring."""
import unittest

from utilities import safe_equal


class SafeEqualTest(unittest.TestCase):
    def test_ignores_only_surrounding_whitespace_and_case(self):
        self.assertTrue(safe_equal("  Four\n", "four"))

    def test_different_duration_is_wrong(self):
        self.assertFalse(safe_equal("32 mins", "35 mins"))

    def test_different_time_is_wrong(self):
        self.assertFalse(safe_equal("3:06 PM", "4:06 PM"))

    def test_different_route_order_is_wrong(self):
        self.assertFalse(safe_equal(
            "Cusco Cathedral -> Qorikancha -> Mercado -> Saqsaywaman",
            "Saqsaywaman -> Cusco Cathedral -> Mercado -> Qorikancha",
        ))


if __name__ == "__main__":
    unittest.main()
