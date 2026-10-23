"""테스트용 DB 준비.

DATABASE_URL(기본 supplier_db) 안에 임시 스키마 pytest_business를 만들고 scripts/seed.py로 채운 뒤,
테스트가 끝나면 스키마를 지운다. 기존 public 스키마의 데이터는 건드리지 않는다.
"""

import importlib.util
import os
import uuid
from pathlib import Path

import psycopg
import pytest

BASE_URL = os.getenv("DATABASE_URL", "postgresql://agent:agent@localhost:5432/supplier_db")
SCHEMA = "pytest_business_" + uuid.uuid4().hex
# server.py와 seed.py가 import 될 때 이 값을 읽으므로, 테스트 모듈보다 먼저 바꿔 둔다
os.environ["DATABASE_URL"] = f"{BASE_URL}{'&' if '?' in BASE_URL else '?'}options=-csearch_path%3D{SCHEMA}"


@pytest.fixture(scope="session", autouse=True)
def seeded_schema():
    try:
        with psycopg.connect(BASE_URL, autocommit=True) as conn:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL에 연결할 수 없어 테스트를 건너뜀 (SETUP.md 3단계 참고)")

    spec = importlib.util.spec_from_file_location("seed", Path(__file__).resolve().parent.parent / "scripts" / "seed.py")
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    # A database test must not overwrite the repository's CSV snapshots.
    seed.dump_csv = lambda *args, **kwargs: None
    try:
        seed.main()
        migrations = Path(__file__).resolve().parents[3] / "backend/src/main/resources/db/business"
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            for name in ("V1__supplier_management.sql", "V2__supplier_contacts.sql", "V3__quote_source.sql"):
                conn.execute((migrations / name).read_text(encoding="utf-8"))
        yield
    finally:
        with psycopg.connect(BASE_URL, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
