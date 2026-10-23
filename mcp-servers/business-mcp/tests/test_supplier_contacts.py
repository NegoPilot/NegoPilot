"""협력사 담당자 테이블(V2, #237): 메일 수신자 제약과 로컬 시연 데이터."""

import os
from pathlib import Path

import psycopg
import pytest

DEMO_SQL = Path(__file__).resolve().parents[3] / "deploy/local/postgres/supplier-contacts-demo.sql"


@pytest.fixture
def conn():
    # 테스트마다 되돌려 다른 테스트의 데이터에 영향을 주지 않는다
    with psycopg.connect(os.environ["DATABASE_URL"]) as c:
        yield c
        c.rollback()


def add(conn, supplier="SUP-0001", name="김담당", email="a@example.test", primary=False, status="ACTIVE"):
    conn.execute("INSERT INTO supplier_contacts (supplier_id, name, email, is_primary, status) VALUES (%s,%s,%s,%s,%s)",
                 (supplier, name, email, primary, status))


def test_demo_sql_gives_every_supplier_one_primary_example_test_contact(conn):
    conn.execute(DEMO_SQL.read_text(encoding="utf-8"))
    suppliers = conn.execute("SELECT count(*) FROM suppliers").fetchone()[0]
    rows = conn.execute("SELECT supplier_id, email FROM supplier_contacts WHERE is_primary").fetchall()
    assert len(rows) == suppliers
    assert all(email == f"sales.{sid.lower()}@example.test" for sid, email in rows)
    conn.execute(DEMO_SQL.read_text(encoding="utf-8"))   # 다시 실행해도 늘지 않는다
    assert conn.execute("SELECT count(*) FROM supplier_contacts").fetchone()[0] == suppliers


def test_only_one_active_primary_per_supplier(conn):
    add(conn, email="a@example.test", primary=True)
    add(conn, email="old@example.test", primary=True, status="INACTIVE")   # 비활성은 세지 않는다
    with pytest.raises(psycopg.errors.UniqueViolation):
        add(conn, email="b@example.test", primary=True)


def test_same_email_differing_only_in_case_is_rejected(conn):
    add(conn, email="Sales@Example.test")
    with pytest.raises(psycopg.errors.UniqueViolation):
        add(conn, email="sales@example.test")


@pytest.mark.parametrize("email", ["no-at-sign", "a b@example.test", "a@nodot", "a@@example.test"])
def test_malformed_email_is_rejected(conn, email):
    with pytest.raises(psycopg.errors.CheckViolation):
        add(conn, email=email)


def test_contacts_follow_supplier_deletion(conn):
    sid = conn.execute("INSERT INTO suppliers (supplier_id, source, name, region, founded_year, biz_no_masked) "
                       "VALUES ('SUP-9999', 'MANUAL', '삭제용', '서울', 2020, '000-00-*****') RETURNING supplier_id").fetchone()[0]
    add(conn, supplier=sid)
    conn.execute("DELETE FROM suppliers WHERE supplier_id = %s", (sid,))
    assert conn.execute("SELECT count(*) FROM supplier_contacts WHERE supplier_id = %s", (sid,)).fetchone()[0] == 0
