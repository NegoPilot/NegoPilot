"""Apply the non-destructive supplier-management migrations (V1 management, V2 contacts, V3 quote sources) without starting Spring.

Requires an explicit DATABASE_URL and the existing eight business tables.
The SQL is shared with Spring Flyway and is safe to run before or after Flyway.
No model calls or external data collection. Never invokes schema.sql or seed.py.
"""

import os
from pathlib import Path

import psycopg

MIGRATIONS = Path(__file__).resolve().parents[3] / "backend/src/main/resources/db/business"
SQL_PATHS = [MIGRATIONS / "V1__supplier_management.sql", MIGRATIONS / "V2__supplier_contacts.sql",
             MIGRATIONS / "V3__quote_source.sql"]
TABLES = ["suppliers", "financials", "capabilities", "quality", "contracts", "quotes", "oems", "rfqs"]


def migrate(dsn: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('negopilot_supplier_management'))")
        count = conn.execute("SELECT count(*) FROM information_schema.tables "
                             "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE' "
                             "AND table_name = ANY(%s)", (TABLES,)).fetchone()[0]
        if count != len(TABLES):
            raise ValueError("Existing business tables are required. Prepare a separate seed database explicitly.")
        for path in SQL_PATHS:
            conn.execute(path.read_text(encoding="utf-8"))


def main() -> None:
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise SystemExit("Set DATABASE_URL explicitly. No database was changed.")
    try:
        migrate(dsn)
    except (ValueError, psycopg.Error):
        raise SystemExit("Supplier migration failed. Check the existing tables and database permissions. Connection details are not printed.") from None
    print("Supplier management migration completed. Existing data and edit history are preserved.")


if __name__ == "__main__":
    main()
