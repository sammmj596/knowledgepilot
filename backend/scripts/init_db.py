#!/usr/bin/env python3
"""Apply SQL schema files to the configured MySQL instance."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.db import execute_script, get_connection  # noqa: E402


def _apply_sql_files(conn, paths: list[Path], *, ignore_duplicate_column: bool = False) -> None:
    for path in paths:
        print(f"Applying {path.name}...")
        try:
            execute_script(conn, path.read_text(encoding="utf-8"))
        except Exception as exc:
            if ignore_duplicate_column and exc.args[0] == 1060:
                print(f"  skip {path.name} (column already exists)")
                conn.rollback()
                continue
            raise


def main() -> None:
    sql_dir = BACKEND_DIR / "sql"
    schema_files = sorted(sql_dir.glob("*.sql"))
    migration_files = sorted((sql_dir / "migrations").glob("*.sql"))
    if not schema_files:
        print("No .sql files found in", sql_dir)
        sys.exit(1)
    with get_connection(database=None) as conn:
        _apply_sql_files(conn, schema_files)
        if migration_files:
            _apply_sql_files(conn, migration_files, ignore_duplicate_column=True)
    print("Database initialized.")


if __name__ == "__main__":
    main()
