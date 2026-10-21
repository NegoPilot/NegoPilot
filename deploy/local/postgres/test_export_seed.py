"""Offline checks for image-build seed rendering. No PostgreSQL is contacted."""

import importlib.util
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("local_seed_export", Path(__file__).with_name("export_seed.py"))
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)
BUSINESS_ROOT = Path(__file__).resolve().parents[3] / "mcp-servers/business-mcp"


class SeedExportTests(unittest.TestCase):
    def test_renders_same_repository_seed_without_database_or_csv_writes(self):
        snapshots = {path.name: path.read_bytes() for path in (BUSINESS_ROOT / "data").glob("*.csv")}
        with patch("socket.socket.connect", side_effect=AssertionError("No network during seed export")):
            first = exporter.render_seed(BUSINESS_ROOT)
            second = exporter.render_seed(BUSINESS_ROOT)
        self.assertEqual(first, second)
        self.assertEqual(first.count("INSERT INTO suppliers ("), 150)
        self.assertEqual(first.count("INSERT INTO rfqs VALUES"), 6)
        self.assertIn("CREATE TABLE suppliers", first)
        self.assertIn("'DART'", first)
        self.assertIn("'MOCK'", first)
        self.assertEqual(snapshots, {path.name: path.read_bytes() for path in (BUSINESS_ROOT / "data").glob("*.csv")})

    def test_quotes_values_as_sql_literals(self):
        capture = exporter.SqlCapture()
        capture.executemany("INSERT INTO example VALUES (%s,%s,%s,%s,%s)", [
            ("O'Brien; DROP TABLE example; --", None, True, 123, date(2026, 10, 21)),
        ])
        self.assertEqual(capture.statements, [
            "INSERT INTO example VALUES ('O''Brien; DROP TABLE example; --',NULL,true,123,'2026-10-21'::date);",
        ])

    def test_rejects_changed_placeholder_shape(self):
        with self.assertRaisesRegex(ValueError, "placeholder count"):
            exporter.SqlCapture().executemany("INSERT INTO example VALUES (%s)", [(1, 2)])


if __name__ == "__main__":
    unittest.main()
