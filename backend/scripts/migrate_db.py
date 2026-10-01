#!/usr/bin/env python3
"""Apply ALTER TABLE migrations to an existing database (skips CREATE TABLE)."""

from __future__ import annotations

import sys
from pathlib import Path

import pymysql

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.db import execute_script, get_connection  # noqa: E402


def main() -> None:
    migrations_dir = BACKEND_DIR / "sql" / "migrations"
    files = sorted(migrations_dir.glob("*.sql"))
    if not files:
        print("No migration files in", migrations_dir)
        sys.exit(1)
    with get_connection(database=None) as conn:
        for path in files:
            print(f"Applying {path.name}...")
            try:
                execute_script(conn, path.read_text(encoding="utf-8"))
            except pymysql.err.OperationalError as exc:
                if exc.args and exc.args[0] == 1060:
                    print(f"  skip (column already exists)")
                    conn.rollback()
                    continue
                raise
    print("Migrations applied.")


if __name__ == "__main__":
    main()
