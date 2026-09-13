import unittest

from reconcile import (
    normalize_control_id,
    normalize_status,
    normalize_completion,
    normalize_date,
    flag_future_date,
)


class TestNormalizeControlId(unittest.TestCase):
    def test_plain_id(self):
        self.assertEqual(normalize_control_id("CTRL-001"), "CTRL-001")

    def test_whitespace(self):
        self.assertEqual(normalize_control_id("CTRL-010 "), "CTRL-010")
        self.assertEqual(normalize_control_id(" CTRL-010"), "CTRL-010")

    def test_bare_number(self):
        self.assertEqual(normalize_control_id(5), "CTRL-005")
        self.assertEqual(normalize_control_id(5.0), "CTRL-005")

    def test_no_dash(self):
        self.assertEqual(normalize_control_id("CTRL5"), "CTRL-005")

    def test_none(self):
        self.assertIsNone(normalize_control_id(None))


class TestNormalizeStatus(unittest.TestCase):
    def test_variants_map_to_canonical(self):
        for raw in ["Complete", "complete", "COMPLETE", "Completed"]:
            status, warn = normalize_status(raw)
            self.assertEqual(status, "Complete")

        for raw in ["In Progress", "in progress", "In-Progress"]:
            status, warn = normalize_status(raw)
            self.assertEqual(status, "In Progress")

        for raw in ["Not Started", "not started", "Not started"]:
            status, warn = normalize_status(raw)
            self.assertEqual(status, "Not Started")

    def test_unrecognized(self):
        status, warn = normalize_status("Blocked")
        self.assertIsNone(status)
        self.assertIsNotNone(warn)

    def test_missing(self):
        status, warn = normalize_status(None)
        self.assertIsNone(status)
        self.assertIn("missing", warn)


class TestNormalizeCompletion(unittest.TestCase):
    def test_numeric_passthrough(self):
        val, warn = normalize_completion(0.6, "In Progress")
        self.assertEqual(val, 0.6)
        self.assertIsNone(warn)

    def test_na_with_not_started_infers_zero(self):
        val, warn = normalize_completion("N/A", "Not Started")
        self.assertEqual(val, 0.0)
        self.assertIsNotNone(warn)

    def test_na_with_ambiguous_status_stays_none(self):
        val, warn = normalize_completion("N/A", "In Progress")
        self.assertIsNone(val)
        self.assertIsNotNone(warn)

    def test_percentage_style_value(self):
        val, warn = normalize_completion(70, "In Progress")
        self.assertEqual(val, 0.7)

    def test_out_of_range(self):
        val, warn = normalize_completion(150, "In Progress")
        self.assertIsNone(val)
        self.assertIsNotNone(warn)


class TestNormalizeDate(unittest.TestCase):
    def test_iso_passthrough(self):
        d, warn = normalize_date("2026-08-15")
        self.assertEqual(d, "2026-08-15")
        self.assertIsNone(warn)

    def test_dd_mm_yyyy(self):
        d, warn = normalize_date("15/08/2026")
        self.assertEqual(d, "2026-08-15")
        self.assertIsNotNone(warn)

    def test_month_name_format(self):
        d, warn = normalize_date("Aug 5 2026")
        self.assertEqual(d, "2026-08-05")

    def test_unix_timestamp(self):
        d, warn = normalize_date(1755302400)
        self.assertEqual(d, "2025-08-16")
        self.assertIn("unix", warn.lower())

    def test_missing(self):
        d, warn = normalize_date(None)
        self.assertIsNone(d)
        self.assertIsNotNone(warn)

    def test_garbage(self):
        d, warn = normalize_date("not a date")
        self.assertIsNone(d)
        self.assertIsNotNone(warn)


class TestFlagFutureDate(unittest.TestCase):
    def test_future_date_flagged(self):
        warn = flag_future_date("2030-01-01")
        self.assertIsNotNone(warn)

    def test_past_before_engagement_flagged(self):
        warn = flag_future_date("2025-08-16")
        self.assertIsNotNone(warn)

    def test_in_window_not_flagged(self):
        warn = flag_future_date("2026-08-20")
        self.assertIsNone(warn)


if __name__ == "__main__":
    unittest.main()
