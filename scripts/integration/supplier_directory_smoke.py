#!/usr/bin/env python3
"""Verify the supplier directory against an isolated seeded Compose stack.

Read-only lookup requests after logging in. Never changes supplier data or calls
an AI model. Use an isolated test account and pass credentials by environment.
"""
from __future__ import annotations

import argparse
from functools import cmp_to_key
import json
import os
from pathlib import Path
from urllib.parse import urlencode

from compose_smoke import Client, SmokeFailure, require

ROOT = "/api/suppliers"
SORT_FIELDS = (
    "name", "region", "founded_year", "monthly_capacity", "defect_ppm",
    "on_time_delivery_rate", "audit_grade", "years_dealing", "payment_days",
    "raw_material_ratio", "latest_quote_date",
)


def value_for(item, key):
    if key == "monthly_capacity":
        return item["max_monthly_capacity"]
    if key in {"defect_ppm", "on_time_delivery_rate", "audit_grade", "years_dealing"}:
        return item["quality"][key]
    if key in {"payment_days", "raw_material_ratio"}:
        return (item["existing_contract"] or {}).get(key)
    if key in {"latest_quote_date", "latest_quote_price"}:
        return (item["latest_quote"] or {}).get("quoted_at" if key.endswith("date") else "unit_price_krw")
    value = item[key]
    return value.lower() if isinstance(value, str) else value


def expected_order(items, key, direction):
    def compare(a, b):
        left, right = value_for(a, key), value_for(b, key)
        if left is None or right is None:
            result = (left is None) - (right is None)
        else:
            result = (left > right) - (left < right)
            if direction == "desc":
                result = -result
        return result or ((a["supplier_id"] > b["supplier_id"]) - (a["supplier_id"] < b["supplier_id"]))
    return [row["supplier_id"] for row in sorted(items, key=cmp_to_key(compare))]


def page(client, params=None, *, path=ROOT):
    response = client.request("GET", path + ("?" + urlencode(params) if params else ""))
    require((response["schema_version"], response["mode"]) == ("0.4", "live"), "Directory must use live v0.4 lookup")
    data = response["data"]
    require(data["returned_count"] == len(data["items"]), "Returned count mismatch")
    require(data["total_pages"] == (data["total"] + data["size"] - 1) // data["size"], "Page total mismatch")
    require(len(data["items"]) == min(data["size"], max(0, data["total"] - data["page"] * data["size"])), "Page length mismatch")
    return data


def collect(client, params=None, *, size=20, path=ROOT):
    params = dict(params or {}) | {"size": size}
    first = page(client, params | {"page": 0}, path=path)
    items = list(first["items"])
    for number in range(1, first["total_pages"]):
        next_page = page(client, params | {"page": number}, path=path)
        require(next_page["total"] == first["total"], "Dataset changed while checking pages")
        items.extend(next_page["items"])
    identity = "quote_id" if path.endswith("/quotes") else "supplier_id"
    require(len(items) == first["total"] and len({row[identity] for row in items}) == len(items), "Page duplication or omission")
    return items, first


def matching_ids(items, **filters):
    result = set()
    for item in items:
        if filters.get("region") and item["region"] != filters["region"]:
            continue
        if filters.get("q") and not any(filters["q"].lower() in item[key].lower() for key in ("name", "supplier_id")):
            continue
        cap_filters = {k: filters[k] for k in ("process", "material") if filters.get(k)}
        if cap_filters and not any(all(cap[key] == value for key, value in cap_filters.items()) for cap in item["matched_capabilities"]):
            continue
        result.add(item["supplier_id"])
    return result


def run(client, expected_total):
    all_items, first = collect(client)
    require(first["total"] == expected_total, "Unexpected seeded supplier total")
    require(first["size"] == 20 and first["sort"] == "name" and first["direction"] == "asc", "Default page or sort differs")
    all_ids = {row["supplier_id"] for row in all_items}
    require(len(all_ids) > 100, "Seed must exercise more than one hundred suppliers")
    cases = [
        {"process": "CNC가공"}, {"material": "AL6061"},
        {"process": "CNC가공", "material": "AL6061"},
        {"q": "정밀"}, {"q": all_items[0]["supplier_id"].lower()},
        {"region": all_items[0]["region"]},
        {"q": "정밀", "material": "AL6061"},
    ]
    filter_counts = []
    for conditions in cases:
        found, data = collect(client, conditions, size=13)
        require({item["supplier_id"] for item in found} == matching_ids(all_items, **conditions), "Filter search differs from complete dataset")
        filter_counts.append({"filters": conditions, "total": data["total"]})
    cnc, cnc_meta = collect(client, {"process": "CNC가공", "material": "AL6061"})
    require(cnc_meta["total"] == 31 and cnc_meta["total_pages"] == 2, "CNC/AL6061 seeded pagination differs")
    for search in ("", "   "):
        require(page(client, {"q": search})["total"] == expected_total, "Blank query must show all suppliers")
    for literal in ("%", "_"):
        found, _ = collect(client, {"q": literal})
        require({item["supplier_id"] for item in found} == matching_ids(all_items, q=literal), "Search wildcard was interpreted")
    for key in SORT_FIELDS:
        for direction in ("asc", "desc"):
            ordered, _ = collect(client, {"sort": key, "direction": direction}, size=17)
            require({item["supplier_id"] for item in ordered} == all_ids, "Sort lost or added suppliers")
            require([item["supplier_id"] for item in ordered] == expected_order(ordered, key, direction), "Global server sort mismatch: " + key + "/" + direction)
    for direction in ("asc", "desc"):
        priced, _ = collect(client, {"process": "CNC가공", "material": "AL6061", "sort": "latest_quote_price", "direction": direction}, size=7)
        require([item["supplier_id"] for item in priced] == expected_order(priced, "latest_quote_price", direction), "Comparable quote price sort differs")
    past = page(client, {"page": first["total_pages"] + 2})
    require(past["total"] == expected_total and not past["items"], "Out of range page lost total")
    require(page(client, {"q": "not-an-existing-supplier-245"})["total"] == 0, "Unknown name must be empty")
    require(page(client, {"limit": 3})["size"] == 3, "Legacy size alias differs")
    for suffix in ("?page=-1", "?size=101", "?limit=3&size=3", "?limit=3&page=0", "?sort=unknown", "?sort=latest_quote_price", "?page=0&page=1"):
        client.request("GET", ROOT + suffix, status=400)
    selected = all_items[0]["supplier_id"]
    detail = client.request("GET", ROOT + "/" + selected)["data"]
    require(detail["supplier_id"] == selected and "founded_year" in detail and "audit_grade" in detail["quality"], "Expanded detail fields missing")
    quotes, quote_meta = collect(client, size=2, path=ROOT + "/" + selected + "/quotes")
    require(quote_meta["supplier_id"] == selected, "Quote owner mismatch")
    require([(q["quoted_at"], q["quote_id"]) for q in quotes] == sorted(((q["quoted_at"], q["quote_id"]) for q in quotes), reverse=True), "Quote order mismatch")
    return {"schema_version": "0.4", "mode": "live", "supplier_total": first["total"], "pages_at_size20": first["total_pages"], "cnc_al6061_total": cnc_meta["total"], "filter_checks": filter_counts, "sort_directions_checked": len(SORT_FIELDS) * 2 + 2, "quote_total_for_selected_supplier": quote_meta["total"], "duplicate_or_missing_ids": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5185")
    parser.add_argument("--expected-total", type=int, default=150)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    client = Client(args.base_url)
    try:
        client.login(os.environ.get("LOCAL_USER_EMAIL", "user@negopilot.local"), os.environ.get("LOCAL_USER_PASSWORD", "LocalUser2026!"))
        report = run(client, args.expected_total)
        if args.report:
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except SmokeFailure as error:
        raise SystemExit(str(error)) from None
    finally:
        if client.csrf:
            client.logout()


if __name__ == "__main__":
    main()
