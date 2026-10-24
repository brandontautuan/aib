from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "research.db"


def connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def initialize() -> None:
    with connection() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company TEXT NOT NULL,
                ceo TEXT,
                source_type TEXT NOT NULL,
                publisher TEXT,
                title TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                published_at TEXT,
                description TEXT,
                content TEXT,
                transcript TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )


def save_result(item: dict[str, Any]) -> int | None:
    fields = ["company", "ceo", "source_type", "publisher", "title", "url", "published_at", "description", "content", "transcript"]
    values = [item.get(field) for field in fields]
    with connection() as conn:
        try:
            cursor = conn.execute(
                f"INSERT INTO results ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
                values,
            )
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            return None


def list_results(company: str | None = None) -> list[dict[str, Any]]:
    with connection() as conn:
        if company:
            rows = conn.execute("SELECT * FROM results WHERE company = ? ORDER BY id DESC", (company,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM results ORDER BY id DESC").fetchall()
    return [dict(row) for row in rows]
