#!/usr/bin/env python3
"""Validate recorded HTTP responses from stdin without saving bodies.

Run with agent/.venv/bin/python. jsonschema is already present in that runtime.
Only response positions and schema paths are printed on validation failure.
"""

import argparse
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]


def validate_records(contract: dict, responses: list) -> list[str]:
    """Return fixed diagnostics and schema locations without printing bodies."""
    failures = []
    if not isinstance(responses, list):
        return ["Recorded responses must be an array"]
    for index, record in enumerate(responses):
        if (not isinstance(record, dict) or not isinstance(record.get("path"), str) or "body" not in record
                or type(record.get("status")) is not int or not isinstance(record.get("method", "post"), str)):
            failures.append(f"Response {index}: invalid response record")
            continue
        try:
            path = urlsplit(record["path"]).path
        except ValueError:
            failures.append(f"Response {index}: invalid response path")
            continue
        path_key = next((candidate for candidate in contract["paths"] if re.fullmatch(
            "/".join("[^/]+" if part.startswith("{") and part.endswith("}") else re.escape(part)
                     for part in candidate.split("/")), path)), None)
        method = record.get("method", "get" if path == "/health" else "post").lower()
        if path_key is None or method not in contract["paths"][path_key]:
            failures.append(f"Response {index}: undocumented path or method")
            continue
        schemas = contract["paths"][path_key][method]["responses"]
        if str(record["status"]) not in schemas:
            failures.append(f"Response {index}: undocumented HTTP status")
            continue
        schema = schemas[str(record["status"])]["content"]["application/json"]["schema"]
        validator = Draft202012Validator({**contract, **schema}, format_checker=FormatChecker())
        errors = list(validator.iter_errors(record["body"]))
        for error in errors:
            failures.append(f"Response {index}: schema path " + "/".join(map(str, error.schema_path)))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=ROOT / "docs/contracts/agent-generation.openapi.json")
    args = parser.parse_args()
    try:
        contract = json.loads(args.contract.read_text())
        responses = json.load(sys.stdin)
    except (OSError, ValueError, UnicodeError):
        print("Unable to read the contract or recorded response JSON.")
        return 2
    failures = validate_records(contract, responses)
    if failures:
        print("\n".join(failures))
        return 1
    print(f"Validated {len(responses)} HTTP responses against the shared OpenAPI contract.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
