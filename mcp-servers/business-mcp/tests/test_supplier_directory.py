"""Directory pagination against real PostgreSQL in conftest's disposable schema."""
import importlib.util
import os
from pathlib import Path

import psycopg
import pytest
from fastmcp.exceptions import ToolError

SPEC = importlib.util.spec_from_file_location("supplier_directory_server", Path(__file__).resolve().parents[1] / "server.py")
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)
REGION = "페이지시험"


@pytest.fixture(scope="module")
def directory(seeded_schema):
    ids = [f"SUP-{800000 + i}" for i in range(152)]
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        for i, sid in enumerate(ids):
            name = ["abc", "ABC", "가나다", "나다라", "A%_!\\ literal", "(주) 가나다"][i] if i < 6 else f"페이지{i:03d}"
            conn.execute("""INSERT INTO suppliers(supplier_id,source,name,region,founded_year,biz_no_masked,
                         employees,iatf16949,iso14001,status,certification_source,capability_source)
                         VALUES (%s,'MANUAL',%s,%s,%s,'111-22-*****',%s,%s,%s,%s,'MANUAL','MANUAL')""",
                         (sid, name, REGION, 1980 + i % 40, None if i % 5 == 0 else 50 + i,
                          i % 2 == 0, i % 3 == 0, "ACTIVE" if i < 151 else "INACTIVE"))
            if i < 150:
                conn.execute("INSERT INTO capabilities VALUES (%s,'프레스','M-A',%s),(%s,'CNC가공','M-B',1)",
                             (sid, (i + 1) * 100, sid))
            if i % 5:
                conn.execute("INSERT INTO quality VALUES (%s,%s,%s,%s,%s)",
                             (sid, i % 7 * 10, 0.900 + i % 10 / 100, "ABCD"[i % 4], i % 15))
            if i % 4:
                conn.execute("INSERT INTO contracts VALUES (%s,%s,true,%s,false)",
                             (sid, 30 + i % 3 * 10, 0.1 + i % 5 / 10))
            if i < 130:
                conn.execute("""INSERT INTO quotes(supplier_id,process,material,unit_price,quoted_at) VALUES
                             (%s,'프레스','M-A',%s,'2026-01-01'),(%s,'프레스','M-A',%s,'2026-02-01'),
                             (%s,'프레스','M-A',%s,'2026-02-01'),(%s,'CNC가공','M-B',1,'2026-03-01')""",
                             (sid, 100 + i, sid, 200 + i, sid, 300 + i, sid))
        for i in range(150):
            conn.execute("INSERT INTO quotes(supplier_id,process,material,unit_price,quoted_at) "
                         "VALUES (%s,'프레스','M-A',%s,'2025-01-01')", (ids[0], 500 + i))
    yield ids
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM suppliers WHERE supplier_id = ANY(%s)", (ids,))
    if server._pool is not None:
        server._pool.close()


def browse(**kwargs):
    return server.list_suppliers_page(region=REGION, **kwargs)


def collect(**kwargs):
    first = browse(size=37, **kwargs)
    rows = list(first["items"])
    for page in range(1, first["total_pages"]):
        rows.extend(browse(size=37, page=page, **kwargs)["items"])
    return rows


def test_over_100_complete_without_capability_or_quality_requirement(directory):
    pages = [browse(size=100, page=number) for number in range(3)]
    assert [p["returned_count"] for p in pages] == [100, 51, 0]
    assert all(p["total"] == 151 and p["total_pages"] == 2 for p in pages)
    all_ids = [row["supplier_id"] for p in pages for row in p["items"]]
    assert len(set(all_ids)) == 151 and set(all_ids) == set(directory[:151])
    no_data = next(row for p in pages for row in p["items"] if row["supplier_id"] == directory[150])
    assert no_data["matched_capabilities"] == [] and no_data["max_monthly_capacity"] is None
    assert no_data["defect_ppm"] is None and no_data["latest_quote"] is None
    assert browse(q=" ")["total"] == 151


def test_independent_optional_filters_match_same_capability_row(directory):
    assert browse(process="프레스")["total"] == 150
    assert browse(material="M-B")["total"] == 150
    assert browse(process="프레스", material="M-B")["total"] == 0
    assert browse(process="CNC가공", min_monthly_capacity=2)["total"] == 0
    result = browse(material="M-A", min_monthly_capacity=15000)
    assert result["total"] == 1 and result["items"][0]["supplier_id"] == directory[149]
    assert result["items"][0]["matched_capabilities"] == [
        {"process": "프레스", "material": "M-A", "monthly_capacity": 15000}]
    assert result["items"][0]["max_monthly_capacity"] == 15000
    assert browse(iatf_required=True, iso14001_required=True)["total"] == 26
    assert browse(process="없는공정")["total"] == 0


def test_partial_search_name_id_case_and_literal_wildcards(directory):
    assert {row["supplier_id"] for row in browse(q="aBc")["items"]} == set(directory[:2])
    assert browse(q=" %_!\\ ")["items"][0]["supplier_id"] == directory[4]
    assert browse(q="%")["total"] == 1
    assert browse(q="_IGNORED")["total"] == 0
    assert browse(q="sup-80014")["total"] == 10
    assert browse(q=" 가나 ")["total"] == 2
    assert browse(q="' OR 1=1 --")["total"] == 0


def _sort_value(row, key):
    if key in {"name", "region"}:
        return row[key].lower()
    return row[{"monthly_capacity": "max_monthly_capacity", "on_time_delivery_rate": "on_time_rate"}.get(key, key)]


@pytest.mark.parametrize("sort", list(server.supplier_directory.SUPPLIER_SORTS))
@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_every_sort_global_stable_nulls_last_across_pages(directory, sort, direction):
    filters = {"process": "프레스", "material": "M-A"} if sort == "latest_quote_price" else {}
    baseline = collect(**filters)
    by_id = sorted(baseline, key=lambda row: row["supplier_id"])
    available = [row for row in by_id if _sort_value(row, sort) is not None]
    unavailable = [row for row in by_id if _sort_value(row, sort) is None]
    expected = sorted(available, key=lambda row: _sort_value(row, sort), reverse=direction == "desc") + unavailable
    actual = collect(sort=sort, direction=direction, **filters)
    assert [row["supplier_id"] for row in actual] == [row["supplier_id"] for row in expected]
    assert len(actual) == len({row["supplier_id"] for row in actual})


def test_latest_quote_uses_matching_filters_and_date_then_id(directory):
    item = browse(q=directory[0], process="프레스", material="M-A")["items"][0]
    assert item["latest_quote"]["unit_price"] == 300 and item["latest_quote"]["quoted_at"] == "2026-02-01"
    unfiltered = browse(q=directory[0])["items"][0]
    assert unfiltered["latest_quote"]["process"] == "CNC가공"
    detail = server.get_supplier(directory[0])
    assert detail["recent_quotes"][0]["quote_id"] == unfiltered["latest_quote"]["quote_id"]
    assert detail["recent_quotes"][1]["quote_id"] == item["latest_quote"]["quote_id"]
    assert detail["created_at"] and detail["updated_at"]


def test_all_quote_history_and_stable_date_ties(directory):
    sid = directory[0]
    pages = [server.list_supplier_quotes(sid, page=number, size=100) for number in range(3)]
    assert [p["returned_count"] for p in pages] == [100, 54, 0]
    assert all(p["supplier_exists"] and p["total"] == 154 for p in pages)
    items = pages[0]["items"] + pages[1]["items"]
    assert len({row["quote_id"] for row in items}) == 154
    assert items == sorted(items, key=lambda row: (row["quoted_at"], row["quote_id"]), reverse=True)
    assert server.list_supplier_quotes(sid, material="M-B")["total"] == 1
    for direction in ("asc", "desc"):
        result = server.list_supplier_quotes(sid, process="프레스", material="M-A", size=100,
                                            sort="unit_price_krw", direction=direction)
        prices = [row["unit_price"] for row in result["items"]]
        assert prices == sorted(prices, reverse=direction == "desc")


def test_quotes_distinguish_missing_inactive_and_empty(directory):
    assert server.list_supplier_quotes(directory[150])["supplier_exists"] is True
    assert server.list_supplier_quotes(directory[150])["total"] == 0
    assert server.list_supplier_quotes(directory[151])["supplier_exists"] is False
    assert server.list_supplier_quotes("SUP-999999")["supplier_exists"] is False


@pytest.mark.parametrize("params", [
    {"page": -1}, {"page": 1_000_001}, {"size": 101}, {"size": 0}, {"page": True},
    {"q": "a" * 101}, {"material": ""}, {"min_monthly_capacity": 0}, {"iatf_required": 1},
    {"sort": "name; DROP TABLE suppliers"}, {"direction": "DESC"}, {"sort": "latest_quote_price"},
])
def test_invalid_tool_parameters(directory, params):
    with pytest.raises(ToolError, match="INVALID_FILTER"):
        browse(**params)


def test_quote_price_requires_both_filters(directory):
    with pytest.raises(ToolError, match="requires process and material"):
        server.list_supplier_quotes(directory[0], sort="unit_price_krw")
