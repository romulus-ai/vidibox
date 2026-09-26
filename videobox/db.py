"""SQLite-Zugriff: Schema und Repository-Funktionen.

Jede Funktion oeffnet ihre eigene kurze Verbindung, damit sie sowohl aus dem
Request-Handler als auch aus dem Download-Worker-Thread sicher nutzbar ist.
"""

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id             TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    source_url     TEXT NOT NULL UNIQUE,
    provider       TEXT,
    duration_s     INTEGER,
    thumbnail_path TEXT,
    file_path      TEXT,
    file_size      INTEGER,
    status         TEXT NOT NULL DEFAULT 'queued',
    error_msg      TEXT,
    created_at     TEXT NOT NULL,
    downloaded_at  TEXT
);

CREATE TABLE IF NOT EXISTS tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    image_path TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS video_tags (
    video_id TEXT    NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id)   ON DELETE CASCADE,
    PRIMARY KEY (video_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
CREATE INDEX IF NOT EXISTS idx_video_tags_tag ON video_tags(tag_id);
"""


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    # ---------- Videos ----------

    def create_video(self, source_url: str, title: str, tag_ids: list[int]) -> dict[str, Any]:
        video_id = uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO videos (id, title, source_url, status, created_at) "
                "VALUES (?, ?, ?, 'queued', ?)",
                (video_id, title, source_url, now_iso()),
            )
            self._set_video_tags(conn, video_id, tag_ids)
        return self.get_video(video_id)  # type: ignore[return-value]

    def get_video(self, video_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
            if row is None:
                return None
            return self._video_with_tags(conn, row)

    def get_video_by_url(self, source_url: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM videos WHERE source_url = ?", (source_url,)
            ).fetchone()
            return self._video_with_tags(conn, row) if row else None

    def list_videos(
        self, status: str | None = None, tag_id: int | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT v.* FROM videos v"
        params: list[Any] = []
        where: list[str] = []
        if tag_id is not None:
            sql += " JOIN video_tags vt ON vt.video_id = v.id"
            where.append("vt.tag_id = ?")
            params.append(tag_id)
        if status is not None:
            where.append("v.status = ?")
            params.append(status)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY v.created_at DESC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._video_with_tags(conn, r) for r in rows]

    def update_video(self, video_id: str, **fields: Any) -> None:
        tag_ids = fields.pop("tag_ids", None)
        with self.connect() as conn:
            if fields:
                cols = ", ".join(f"{k} = ?" for k in fields)
                conn.execute(f"UPDATE videos SET {cols} WHERE id = ?", (*fields.values(), video_id))
            if tag_ids is not None:
                self._set_video_tags(conn, video_id, tag_ids)

    def delete_video(self, video_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))

    def next_queued_video(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM videos WHERE status = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            return self._video_with_tags(conn, row) if row else None

    def reset_downloading_to_queued(self) -> int:
        with self.connect() as conn:
            cur = conn.execute("UPDATE videos SET status = 'queued' WHERE status = 'downloading'")
            return cur.rowcount

    def count_by_status(self) -> dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS n FROM videos GROUP BY status")
            return {r["status"]: r["n"] for r in rows}

    def _set_video_tags(self, conn: sqlite3.Connection, video_id: str, tag_ids: list[int]) -> None:
        conn.execute("DELETE FROM video_tags WHERE video_id = ?", (video_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO video_tags (video_id, tag_id) VALUES (?, ?)",
            [(video_id, t) for t in tag_ids],
        )

    @staticmethod
    def _video_with_tags(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        video = dict(row)
        tags = conn.execute(
            "SELECT tag_id FROM video_tags WHERE video_id = ? ORDER BY tag_id", (video["id"],)
        ).fetchall()
        video["tag_ids"] = [t["tag_id"] for t in tags]
        return video

    # ---------- Tags ----------

    def create_tag(self, name: str, sort_order: int = 0) -> dict[str, Any]:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO tags (name, sort_order, created_at) VALUES (?, ?, ?)",
                (name, sort_order, now_iso()),
            )
            tag_id = cur.lastrowid
        return self.get_tag(tag_id)  # type: ignore[arg-type, return-value]

    def get_tag(self, tag_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tags WHERE id = ?", (tag_id,)).fetchone()
            return dict(row) if row else None

    def list_tags(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM tags ORDER BY sort_order, name").fetchall()
            return [dict(r) for r in rows]

    def update_tag(self, tag_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.connect() as conn:
            conn.execute(f"UPDATE tags SET {cols} WHERE id = ?", (*fields.values(), tag_id))

    def delete_tag(self, tag_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))

    def tag_video_counts(self, status: str = "downloaded") -> dict[int, int]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT vt.tag_id, COUNT(*) AS n FROM video_tags vt "
                "JOIN videos v ON v.id = vt.video_id WHERE v.status = ? GROUP BY vt.tag_id",
                (status,),
            ).fetchall()
            return {r["tag_id"]: r["n"] for r in rows}

    def first_thumbnail_for_tag(self, tag_id: int) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT v.thumbnail_path FROM videos v JOIN video_tags vt ON vt.video_id = v.id "
                "WHERE vt.tag_id = ? AND v.status = 'downloaded' AND v.thumbnail_path IS NOT NULL "
                "ORDER BY v.created_at LIMIT 1",
                (tag_id,),
            ).fetchone()
            return row["thumbnail_path"] if row else None

    def tags_exist(self, tag_ids: list[int]) -> bool:
        if not tag_ids:
            return True
        placeholders = ",".join("?" * len(tag_ids))
        with self.connect() as conn:
            n = conn.execute(
                f"SELECT COUNT(*) FROM tags WHERE id IN ({placeholders})", tag_ids
            ).fetchone()[0]
            return n == len(set(tag_ids))
