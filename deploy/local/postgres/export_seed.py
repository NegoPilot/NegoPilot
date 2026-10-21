"""Render the repository's deterministic seed as SQL without connecting to a DB.

The seed's cursor operations are captured during the image build. This keeps
schema, column order, CSV input and generated data identical to scripts/seed.py.
The generated artifact is applied only by the guarded first-init hook.
"""

import argparse
import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from psycopg import sql


class SqlCapture:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def __enter__(self) -> "SqlCapture":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def cursor(self) -> "SqlCapture":
        return self

    def execute(self, statement: str) -> None:
        self.statements.append(statement.rstrip().rstrip(";") + ";")

    def executemany(self, statement: str, rows: object) -> None:
        parts = statement.split("%s")
        for row in rows:
            if len(parts) != len(row) + 1:
                raise ValueError("Seed SQL placeholder count does not match row")
            rendered = parts[0]
            for value, suffix in zip(row, parts[1:]):
                rendered += sql.Literal(value).as_string() + suffix
            self.execute(rendered)


def render_seed(business_root: Path) -> str:
    spec = importlib.util.spec_from_file_location("local_business_seed", business_root / "scripts/seed.py")
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load repository seed")
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    capture = SqlCapture()
    # Neither a real connection nor CSV writes are allowed at image-build time.
    with patch.object(seed.psycopg, "connect", return_value=capture), \
            patch.object(seed, "dump_csv"), redirect_stdout(io.StringIO()):
        seed.main()
    return "-- Generated from repository seed.py and bundled DART CSV snapshots.\n" + "\n".join(capture.statements) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("business_root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(render_seed(args.business_root), encoding="utf-8")
