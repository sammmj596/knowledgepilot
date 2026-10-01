from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Iterator, cast

import pymysql
from pymysql.connections import Connection
from pymysql.cursors import DictCursor

from app.config import MYSQL_DATABASE, MYSQL_HOST, MYSQL_PASSWORD, MYSQL_PORT, MYSQL_USER

_STATEMENT_SPLIT = re.compile(r";\s*\n")


def get_connection(*, database: str | None = MYSQL_DATABASE) -> Connection:
    kwargs: dict = {
        "host": MYSQL_HOST,
        "port": MYSQL_PORT,
        "user": MYSQL_USER,
        "password": MYSQL_PASSWORD,
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "autocommit": False,
    }
    if database:
        kwargs["database"] = database
    return pymysql.connect(**kwargs)


@contextmanager
def db_cursor(*, database: str | None = MYSQL_DATABASE) -> Iterator[DictCursor]:
    conn = get_connection(database=database)
    try:
        with conn.cursor() as cur:
            yield cast(DictCursor, cur)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def execute_script(conn: Connection, script: str) -> None:
    statements = [s.strip() for s in _STATEMENT_SPLIT.split(script) if s.strip()]
    with conn.cursor() as cur:
        for stmt in statements:
            cur.execute(stmt)
    conn.commit()
