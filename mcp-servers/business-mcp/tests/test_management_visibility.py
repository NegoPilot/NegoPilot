"""Real SQL checks in conftest's isolated and migrated schema."""

import importlib.util
import json
import os
from pathlib import Path

import psycopg
import pytest
from fastmcp.exceptions import ToolError

SPEC = importlib.util.spec_from_file_location("management_visibility_server", Path(__file__).resolve().parents[1] / "server.py")
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


@pytest.fixture
def supplier():
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("INSERT INTO suppliers(supplier_id,source,name,region,founded_year,biz_no_masked,iatf16949) "
                     "VALUES ('SUP-10001','MANUAL','관리시험기업','시험지역',2020,'111-22-*****',true)")
        conn.execute("INSERT INTO capabilities VALUES ('SUP-10001','프레스','TEST_MATERIAL',1000000)")
        conn.execute("INSERT INTO quality VALUES ('SUP-10001',0,1,'A',0)")
        conn.execute("INSERT INTO quotes(supplier_id,process,material,unit_price,quoted_at) "
                     "VALUES ('SUP-10001','프레스','TEST_MATERIAL',1,'2026-01-01')")
        conn.execute("INSERT INTO rfqs VALUES ('RFQ-ADMIN-TEST','OEM-A','TEST','TEST','프레스','TEST_MATERIAL',1200,100,NULL,'2026-12-31')")
    yield "SUP-10001"
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM rfqs WHERE rfq_id='RFQ-ADMIN-TEST'")
        conn.execute("DELETE FROM suppliers WHERE supplier_id='SUP-10001'")


def test_large_exact_id_and_inactive_filter_across_all_agent_paths(supplier):
    assert server.find_suppliers(supplier)["matches"][0]["supplier_id"] == supplier
    assert server.get_supplier("10001")["supplier_id"] == supplier
    assert server.search_suppliers(material="TEST_MATERIAL")[0]["supplier_id"] == supplier
    assert server.rank_suppliers("RFQ-ADMIN-TEST")["ranked"][0]["supplier_id"] == supplier
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("UPDATE suppliers SET status='INACTIVE' WHERE supplier_id=%s", (supplier,))
    assert server.find_suppliers(supplier)["matches"] == []
    assert server.find_suppliers("관리시험기업")["matches"] == []
    assert supplier not in {row["supplier_id"] for row in server.search_suppliers(limit=100, distinct_suppliers=True)}
    assert server.rank_suppliers("RFQ-ADMIN-TEST")["ranked"] == []
    assert server.rank_suppliers("RFQ-ADMIN-TEST")["excluded"] == []
    for name in (supplier, "관리시험기업"):
        with pytest.raises(ToolError):
            server.get_supplier(name)
        with pytest.raises(ToolError):
            server.check_offer(name, "RFQ-ADMIN-TEST", 100, 30, True)
    catalog = json.loads(server.catalog())
    assert "TEST_MATERIAL" not in catalog["processes"]["프레스"]
    assert "시험지역" not in {row["region"] for row in catalog["regions"]}
    assert all(row["source"] != "MANUAL" for row in catalog["suppliers_by_source"])


def test_missing_quality_and_quote_are_visible_but_not_silently_ranked(supplier):
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM quality WHERE supplier_id=%s", (supplier,))
        conn.execute("DELETE FROM quotes WHERE supplier_id=%s", (supplier,))
    assert server.get_supplier(supplier)["defect_ppm"] is None
    assert server.search_suppliers(material="TEST_MATERIAL")[0]["supplier_id"] == supplier
    ranked = server.rank_suppliers("RFQ-ADMIN-TEST")
    assert ranked["ranked"] == []
    assert ranked["excluded"][0]["reason"] == "품질 또는 견적 자료 미등록"


def test_standalone_migration_preserves_manual_edits_and_existing_audit(supplier):
    spec = importlib.util.spec_from_file_location("management_migration", Path(__file__).resolve().parents[1] / "scripts/migrate_management.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("UPDATE suppliers SET status='INACTIVE',version=9,certification_source='MANUAL' WHERE supplier_id=%s", (supplier,))
        conn.execute("INSERT INTO supplier_change_events(supplier_id,actor_user_id,action,after_data) "
                     "VALUES (%s,'00000000-0000-0000-0000-000000000001','TEST','{}')", (supplier,))
    migration.migrate(os.environ["DATABASE_URL"])
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        assert conn.execute("SELECT status,version,certification_source FROM suppliers WHERE supplier_id=%s", (supplier,)).fetchone() == ("INACTIVE", 9, "MANUAL")
        assert conn.execute("SELECT count(*) FROM supplier_change_events WHERE supplier_id=%s", (supplier,)).fetchone()[0] == 1
        conn.execute("DELETE FROM supplier_change_events WHERE supplier_id=%s", (supplier,))


def test_quote_sources_cover_all_quotes_and_same_date_quotes_prefer_latest_id(supplier):
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        for price in (2, 3, 4, 5, 6):
            conn.execute("INSERT INTO quotes(supplier_id,process,material,unit_price,quoted_at,source) "
                         "VALUES ('SUP-10001','프레스','TEST_MATERIAL',%s,'2026-02-01','MANUAL')", (price,))
    # 2026-01-01의 MOCK 견적은 최근 5건 밖이어도 출처에 포함된다 (#249)
    detail = server.get_supplier(supplier)
    assert [q["unit_price"] for q in detail["recent_quotes"]] == [6, 5, 4, 3, 2]
    assert detail["quote_sources"] == ["MANUAL", "MOCK"]
    assert server.search_suppliers(material="TEST_MATERIAL")[0]["quote_sources"] == ["MANUAL", "MOCK"]
    assert server.latest_quote(supplier, "프레스", "TEST_MATERIAL") == 6
    assert server.rank_suppliers("RFQ-ADMIN-TEST")["ranked"][0]["last_price"] == 6
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM quotes WHERE supplier_id=%s AND source='MOCK'", (supplier,))
    assert server.get_supplier(supplier)["quote_sources"] == ["MANUAL"]
    assert server.search_suppliers(material="TEST_MATERIAL")[0]["quote_sources"] == ["MANUAL"]
