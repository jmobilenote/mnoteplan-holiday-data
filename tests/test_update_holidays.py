import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.update_holidays import UpdateError, parse_response, update


def xml(items, count=None, code="00"):
    count = len(items) if count is None else count
    return (f"<response><header><resultCode>{code}</resultCode></header><body><items>{''.join(items)}</items>"
            f"<totalCount>{count}</totalCount></body></response>").encode()


def item(date, name, flag="Y"):
    return f"<item><locdate>{date}</locdate><dateName>{name}</dateName><isHoliday>{flag}</isHoliday></item>"


class HolidayPublisherTests(unittest.TestCase):
    def test_filters_flags_and_orders(self):
        days = parse_response(xml([item("20261009", "Hangul Day"), item("20260101", "New Year"),
                                   item("20260102", "Not holiday", "N")]), 2026)
        self.assertEqual(["2026-01-01", "2026-10-09"], [day["date"] for day in days])

    def test_rejects_errors_partial_pages_wrong_year_and_entities(self):
        for raw, year in [
            (xml([], code="30"), 2026),
            (xml([item("20260101", "New Year")], count=2), 2026),
            (xml([item("20270101", "New Year")]), 2026),
            (b"<!DOCTYPE r [<!ENTITY x SYSTEM 'file:///private'>]>" + xml([]), 2026),
        ]:
            with self.subTest(year=year), self.assertRaises(UpdateError):
                parse_response(raw, year)

    def test_versions_only_changed_year_and_increments_data_version(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            today = dt.date(2026, 10, 18)
            payload = {2026: [{"date": "2026-10-03", "name": "Foundation Day"}],
                       2027: [{"date": "2027-01-01", "name": "New Year"}]}
            first = update(output, "secret", today, lambda year, key: payload[year])
            self.assertEqual(1, first["dataVersion"])
            self.assertEqual("2026-10-03", json.loads((output / first["countries"]["kr"]["2026"]["path"]).read_text())
                             ["holidays"][0]["date"])
            digest = first["countries"]["kr"]["2026"]["sha256"]
            path = output / first["countries"]["kr"]["2026"]["path"]
            self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
            second = update(output, "secret", today, lambda year, key: payload[year])
            self.assertEqual(1, second["dataVersion"])
            payload[2026] = [{"date": "2026-10-04", "name": "Changed Foundation Day"}]
            third = update(output, "secret", today, lambda year, key: payload[year])
            self.assertEqual(2, third["dataVersion"])
            self.assertEqual(2, third["countries"]["kr"]["2026"]["dataVersion"])
            self.assertEqual("2026-10-04", json.loads(
                (output / third["countries"]["kr"]["2026"]["path"]).read_text())["holidays"][0]["date"])

    def test_failure_does_not_mutate_existing_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            today = dt.date(2026, 10, 18)
            update(output, "secret", today, lambda year, key: [])
            before = {str(path.relative_to(output)): path.read_bytes() for path in output.rglob("*") if path.is_file()}
            calls = 0
            def fail_after_first(year, key):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise UpdateError("simulated failure")
                return []
            with self.assertRaises(UpdateError):
                update(output, "secret", today, fail_after_first)
            after = {str(path.relative_to(output)): path.read_bytes() for path in output.rglob("*") if path.is_file()}
            self.assertEqual(before, after)

    def test_explicit_year_window_publishes_all_requested_years(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            calls = []
            manifest = update(output, "secret", dt.date(2026, 10, 18),
                              lambda year, key: calls.append(year) or
                              [{"date": f"{year}-01-01", "name": "New Year"}], years=list(range(2006, 2047)))
            self.assertEqual(list(range(2006, 2047)), calls)
            self.assertEqual({str(year) for year in range(2006, 2047)},
                             set(manifest["countries"]["kr"]))

    def test_empty_future_years_are_not_published_as_holiday_free(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            manifest = update(output, "secret", dt.date(2026, 10, 18),
                              lambda year, key: ([{"date": "2026-01-01", "name": "New Year"}]
                                                 if year == 2026 else []), years=[2026, 2046])
            self.assertEqual({"2026"}, set(manifest["countries"]["kr"]))

    def test_explicit_year_failure_identifies_year_and_preserves_existing_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            update(output, "secret", dt.date(2026, 10, 18), lambda year, key: [])
            before = {str(path.relative_to(output)): path.read_bytes() for path in output.rglob("*") if path.is_file()}
            def fail(year, key):
                if year == 2007:
                    raise UpdateError("API error")
                return []
            with self.assertRaisesRegex(UpdateError, "2007"):
                update(output, "secret", dt.date(2026, 10, 18), fail, years=[2006, 2007, 2008])
            after = {str(path.relative_to(output)): path.read_bytes() for path in output.rglob("*") if path.is_file()}
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
