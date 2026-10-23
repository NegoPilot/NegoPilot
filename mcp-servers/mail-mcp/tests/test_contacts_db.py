"""supplier_contacts 조회 (실제 PostgreSQL). DB가 없으면 건너뛴다.

DATABASE_URL(기본 supplier_db) 안에 임시 스키마를 만들고 V2 migration을 적용한 뒤 지운다.
"""

import os
import uuid
from pathlib import Path

import psycopg
import pytest

import offer_mail as om

BASE_URL = os.getenv("DATABASE_URL", "postgresql://agent:agent@localhost:5432/supplier_db")
V2 = Path(__file__).resolve().parents[3] / "backend/src/main/resources/db/business/V2__supplier_contacts.sql"


@pytest.fixture(scope="module")
def dsn():
    schema = "pytest_mail_" + uuid.uuid4().hex
    try:
        admin = psycopg.connect(BASE_URL, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL에 연결할 수 없어 건너뜀")
    url = f"{BASE_URL}{'&' if '?' in BASE_URL else '?'}options=-csearch_path%3D{schema}"
    with admin:
        admin.execute(f"CREATE SCHEMA {schema}")
        try:
            with psycopg.connect(url) as conn:
                conn.execute("CREATE TABLE suppliers (supplier_id TEXT PRIMARY KEY, name TEXT NOT NULL)")
                conn.execute(V2.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO suppliers VALUES ('SUP-0060', '온새프레스'), ('SUP-0001', '담당자없음')")
                conn.execute("""INSERT INTO supplier_contacts (supplier_id, name, email, is_primary, status) VALUES
                    ('SUP-0060', '김영업', 'sales@onsae.example.test', TRUE, 'ACTIVE'),
                    ('SUP-0060', '이구매', 'buy@onsae.example.test', FALSE, 'ACTIVE'),
                    ('SUP-0060', '퇴사자', 'old@onsae.example.test', FALSE, 'INACTIVE')""")
            yield url
        finally:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")


def ids(dsn):
    with psycopg.connect(dsn) as conn:
        return dict(conn.execute("SELECT email, contact_id FROM supplier_contacts").fetchall())


def test_primary_contact_by_default(dsn):
    c = om.ContactDirectory(dsn).find("SUP-0060", None)
    assert (c.name, c.email, c.supplier_name) == ("김영업", "sales@onsae.example.test", "온새프레스")


def test_specific_active_contact(dsn):
    c = om.ContactDirectory(dsn).find("SUP-0060", ids(dsn)["buy@onsae.example.test"])
    assert c.email == "buy@onsae.example.test"


@pytest.mark.parametrize("supplier, who", [("SUP-0001", None), ("SUP-0060", "old@onsae.example.test"),
                                           ("SUP-0001", "buy@onsae.example.test")])
def test_missing_inactive_or_other_suppliers_contact(dsn, supplier, who):
    # 다른 협력사의 담당자 ID를 넘겨도 받지 않는다
    with pytest.raises(om.MailError) as e:
        om.ContactDirectory(dsn).find(supplier, ids(dsn)[who] if who else None)
    assert e.value.code == "RECIPIENT_MISSING"
