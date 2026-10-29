"""SQLite metadata repository. File bytes live outside the database."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def connection(root: Path):
    db = sqlite3.connect(root / "office_agent.db", timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(root: Path):
    (root / "uploads").mkdir(parents=True, exist_ok=True)
    with connection(root) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS files (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, size INTEGER NOT NULL,
            storage_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
        )""")

        columns = {row[1] for row in db.execute("PRAGMA table_info(files)")}
        if "source_file_id" not in columns:
            db.execute("ALTER TABLE files ADD COLUMN source_file_id TEXT")


def insert(root: Path, record: dict):
    with connection(root) as db:
        db.execute(
            "INSERT INTO files (id, name, size, storage_key, created_at, source_file_id) VALUES (:id, :name, :size, :storage_key, :created_at, :source_file_id)",
            {"source_file_id": None, **record},
        )


def list_files(root: Path, limit: int, offset: int):
    with connection(root) as db:
        items = db.execute(
            "SELECT * FROM files ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        total = db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        return {"items": [dict(item) for item in items], "total": total}


def get(root: Path, file_id: str):
    with connection(root) as db:
        row = db.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        return dict(row) if row else None


def delete_file(root: Path, file_id: str):
    """Delete metadata and bytes; restore bytes if the transaction fails."""
    original = None
    path = None
    removed = False
    try:
        with connection(root) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
            if row is None:
                return False
            path = root / "uploads" / row["storage_key"]
            if path.exists():
                original = path.read_bytes()
            db.execute("DELETE FROM files WHERE id = ?", (file_id,))
            if original is not None:
                path.unlink()
                removed = True
        return True
    except Exception:
        if removed and path is not None and original is not None:
            path.write_bytes(original)
        raise
