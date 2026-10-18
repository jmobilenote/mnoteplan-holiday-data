#!/usr/bin/env python3
"""Fetch KASI public holidays and write immutable, checksummed JSON snapshots."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ENDPOINT = "https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo"
MAX_RESPONSE = 1_048_576


class UpdateError(Exception):
    pass


def parse_response(raw: bytes, year: int) -> list[dict]:
    if len(raw) > MAX_RESPONSE or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise UpdateError("Invalid or oversized API response")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise UpdateError("API returned invalid XML") from exc
    header = root.find(".//header")
    code = header.findtext("resultCode") if header is not None else None
    if code != "00":
        raise UpdateError("KASI API returned an error; check the key and approval in GitHub Actions")
    total = root.findtext(".//totalCount")
    try:
        total_count = int(total or "")
    except ValueError as exc:
        raise UpdateError("API response has no valid totalCount") from exc
    items = root.findall(".//item")
    if not 0 <= total_count <= 100 or len(items) != total_count:
        raise UpdateError("API response is incomplete; existing published data was not changed")
    holidays = []
    seen = set()
    for item in items:
        try:
            day = dt.datetime.strptime(item.findtext("locdate", ""), "%Y%m%d").date()
            name = (item.findtext("dateName") or "").strip()
            flag = item.findtext("isHoliday")
        except ValueError as exc:
            raise UpdateError("API response contains an invalid date") from exc
        if day.year != year or not name or len(name) > 200 or flag not in ("Y", "N"):
            raise UpdateError("API response contains invalid holiday fields")
        if flag == "Y" and (day.isoformat(), name) not in seen:
            holidays.append({"date": day.isoformat(), "name": name})
            seen.add((day.isoformat(), name))
    return sorted(holidays, key=lambda item: (item["date"], item["name"]))


def fetch_year(year: int, key: str) -> list[dict]:
    query = urllib.parse.urlencode({"serviceKey": key, "solYear": str(year), "numOfRows": "100", "pageNo": "1"})
    request = urllib.request.Request(f"{ENDPOINT}?{query}", headers={"Accept": "application/xml"})
    try:
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(request, timeout=30) as response:
            raw = response.read(MAX_RESPONSE + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Do not print request URLs: the query contains the private service key.
        raise UpdateError("Could not connect to KASI holiday API") from exc
    return parse_response(raw, year)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        # The service key is a query parameter. Never forward it to another host.
        return None


def read_manifest(path: Path) -> dict:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("schemaVersion") != 1 or not isinstance(manifest.get("countries"), dict):
            raise ValueError
        return manifest
    except FileNotFoundError:
        return {"schemaVersion": 1, "dataVersion": 0, "updatedAt": None,
                "checkedAt": None, "countries": {"kr": {}}}
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise UpdateError("Existing manifest is invalid; refusing to overwrite published data") from exc


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def update(output: Path, key: str, today: dt.date | None = None, fetch=fetch_year,
           years: list[int] | None = None) -> dict:
    today = today or dt.datetime.now(dt.timezone.utc).date()
    manifest_path = output / "manifest.json"
    old = read_manifest(manifest_path)
    previous_kr = old["countries"].get("kr", {})
    countries = dict(old["countries"])
    kr = dict(previous_kr)
    changed = False
    checked_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()

    # Fetch and validate everything before publishing any file. A failed year leaves
    # the previous manifest and immutable snapshots usable.
    requested_years = years if years is not None else [today.year, today.year + 1]
    if (not requested_years or len(set(requested_years)) != len(requested_years)
            or any(year < 1900 or year > 9999 for year in requested_years)):
        raise UpdateError("Requested year range is invalid")
    results = {}
    for year in requested_years:
        try:
            results[year] = fetch(year, key)
        except UpdateError as exc:
            raise UpdateError(f"Failed to fetch {year}: {exc}") from exc
    snapshots = {}
    for year, holidays in results.items():
        previous = previous_kr.get(str(year), {})
        if not holidays:
            # KASI returns an empty successful response for years outside its
            # published calendar range. Do not present that as a known holiday-free year.
            old_data = None
            if previous.get("path"):
                try:
                    old_data = json.loads((output / previous["path"]).read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    old_data = None
            if old_data is not None and old_data.get("holidays") == []:
                kr.pop(str(year), None)
                try:
                    (output / previous["path"]).unlink()
                except OSError:
                    pass
                changed = True
            print(f"No holiday entries for {year}; omitted from the published manifest")
            continue
        old_data = None
        if previous.get("path"):
            try:
                old_data = json.loads((output / previous["path"]).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                old_data = None
        if (old_data and old_data.get("schemaVersion") == 1 and old_data.get("year") == year
                and old_data.get("holidays") == holidays and previous.get("sha256")
                and hashlib.sha256((output / previous["path"]).read_bytes()).hexdigest() == previous["sha256"]):
            kr[str(year)] = {**previous, "checkedAt": checked_at}
            continue
        changed = True
        version = int(previous.get("dataVersion", 0)) + 1
        data = {"schemaVersion": 1, "country": "kr", "year": year,
                "dataVersion": version, "updatedAt": checked_at, "holidays": holidays}
        encoded = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        relative = f"kr/{year}.v{version}.json"
        snapshots[relative] = encoded
        kr[str(year)] = {"dataVersion": version, "updatedAt": checked_at, "checkedAt": checked_at,
                         "path": relative, "sha256": digest}
    for relative, encoded in snapshots.items():
        write(output / relative, encoded)
    countries["kr"] = kr
    manifest = {"schemaVersion": 1, "dataVersion": int(old.get("dataVersion", 0)) + int(changed),
                "updatedAt": checked_at if changed else old.get("updatedAt"),
                "checkedAt": checked_at, "countries": countries}
    # Publish the manifest last: readers see either the old complete set or the new one.
    write(manifest_path, (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data"))
    parser.add_argument("--year-start", type=int)
    parser.add_argument("--year-end", type=int)
    args = parser.parse_args()
    key = os.environ.get("KASI_SERVICE_KEY", "").strip()
    if not key:
        print("Missing GitHub Actions secret KASI_SERVICE_KEY", file=sys.stderr)
        return 2
    if (args.year_start is None) != (args.year_end is None):
        print("Both --year-start and --year-end must be provided", file=sys.stderr)
        return 2
    years = None
    if args.year_start is not None:
        if args.year_start > args.year_end or args.year_end - args.year_start > 99:
            print("Requested year range is invalid (maximum 100 years)", file=sys.stderr)
            return 2
        years = list(range(args.year_start, args.year_end + 1))
    try:
        manifest = update(args.output, key, years=years)
    except UpdateError as exc:
        print(f"Holiday update failed: {exc}", file=sys.stderr)
        return 1
    print("Holiday manifest updated", json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
