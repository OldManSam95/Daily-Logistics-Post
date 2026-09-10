import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import sitrep


class SitrepTests(unittest.TestCase):
    def setUp(self):
        self.headers = ["Item", "Category", "Faction", "Unlocked", "Production Method", "Include in Sitrep", "Primary Sitrep Location", "Secondary Sitrep Location"]
        self.config = [["Setting", "Value"], ["Faction", "Warden"], ["Timezone", "Europe/London"], ["War Day 1 Date", "25 Aug 2026"], ["MPF Queue Link", "https://discord.com/channels/123/456"], ["MPF Locations", "Fallback"], ["Factory Locations", "Fallback"], ["Refinery Locations", "Fallback"]]
        self.rows = [
            ["40mm", "Heavy Ammunition", "Warden", "Yes", "MPF", "Yes", "Tine, Speaking Woods", "Tine"],
            ["7.62mm", "Small Arms", "Warden", "Yes", "Factory / MPF", "Yes", "Tine", "Speaking Woods"],
            ["Locked", "Small Arms", "Warden", "Unknown", "Factory / MPF", "Yes", "Wrong", ""],
            ["Excluded", "Small Arms", "Warden", "Yes", "Factory / MPF", "No", "Wrong", ""],
            ["Other faction", "Small Arms", "Colonial", "Yes", "Factory", "Yes", "Wrong", ""],
            ["Maintenance Supplies", "Resource", "Warden", "Yes", "MPF", "Yes", "Tine", ""],
            ["Basic Materials", "", "Warden", "Yes", "Refinery", "Yes", "", ""],
        ]

    def build(self):
        return sitrep.generate([self.headers] + self.rows, self.config, date(2026, 9, 10))

    def test_filter_order_locations_and_day(self):
        message = self.build()
        self.assertTrue(message.startswith("# Daily Logistics Sitrep - Day 17\n"))
        mpf, factory = message.split(sitrep.HEADERS["Factory"])
        self.assertIn("40mm", mpf)
        self.assertNotIn("40mm", factory)
        self.assertLess(mpf.index("**Small Arms:"), mpf.index("**Heavy Ammunition:"))
        self.assertLess(mpf.index("**Heavy Ammunition:"), mpf.index("**Resources:"))
        self.assertIn("**Locations:** Tine, Speaking Woods", mpf)
        self.assertIn("**Locations:** Fallback", factory)
        for excluded in ("Locked", "Excluded", "Other faction", "Wrong", "**Vehicles:"):
            self.assertNotIn(excluded, message)

    def test_bad_inputs_stop_generation(self):
        for rows in ([], [self.headers], [["Item"]]):
            with self.assertRaises(sitrep.SitrepError):
                sitrep.generate(rows, self.config, date(2026, 9, 10))
        self.rows[0][1] = "Misspelled category"
        with self.assertRaises(sitrep.SitrepError):
            self.build()

    def test_dates_and_london_midnight(self):
        self.assertEqual(sitrep.parse_start(46259), date(2026, 8, 25))
        self.assertEqual(sitrep.parse_start("2026-08-25"), date(2026, 8, 25))
        self.assertEqual(datetime(2026, 9, 9, 23, 30, tzinfo=timezone.utc).astimezone(sitrep.LONDON).date(), date(2026, 9, 10))
        with self.assertRaises(sitrep.SitrepError):
            sitrep.generate([self.headers] + self.rows, self.config, date(2026, 8, 24))

    def test_split_limits_and_no_lost_characters(self):
        for message in (self.build(), "😀" * 2200, ("Item, " * 1000).strip(), "First\n\n" + "x" * 4500):
            chunks = sitrep.split_message(message)
            self.assertTrue(all(0 < sitrep.discord_length(c) <= 2000 for c in chunks))
            self.assertEqual("".join("".join(chunks).split()), "".join(message.split()))

    def test_default_preview_never_sends(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            try:
                os.chdir(directory)
                with patch.dict(os.environ, {}, clear=True), patch("sys.argv", ["sitrep.py"]), patch.object(sitrep, "read_sheet", return_value=([self.headers] + self.rows, self.config)), patch.object(sitrep, "send") as send:
                    sitrep.main()
                    send.assert_not_called()
                    self.assertTrue(Path("sitrep-preview.txt").exists())
            finally:
                os.chdir(previous)

    def test_failed_read_never_uses_old_file_or_sends(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            try:
                os.chdir(directory)
                Path("sitrep-message.txt").write_text("old Day 15")
                with patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": "1"}), patch("sys.argv", ["sitrep.py", "--send"]), patch.object(sitrep, "read_sheet", side_effect=sitrep.SitrepError("Failed read")), patch.object(sitrep, "send") as send:
                    with self.assertRaises(sitrep.SitrepError):
                        sitrep.main()
                    send.assert_not_called()
                    self.assertFalse(Path("sitrep-preview.txt").exists())
            finally:
                os.chdir(previous)

    def test_rerun_cannot_post_again(self):
        with patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": "2"}), patch("sys.argv", ["sitrep.py", "--send"]), patch.object(sitrep, "read_sheet") as read:
            with self.assertRaises(sitrep.SitrepError):
                sitrep.main()
            read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
