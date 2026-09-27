"""SQLite-Zugriff: Schema und Repository-Funktionen.

Jede Funktion oeffnet ihre eigene kurze Verbindung, damit sie sowohl aus dem
Request-Handler als auch aus dem Download-Worker-Thread sicher nutzbar ist.
"""

import hashlib
import re
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
    attempts       INTEGER NOT NULL DEFAULT 0,
    import_id      TEXT REFERENCES imports(id) ON DELETE SET NULL,
    created_at     TEXT NOT NULL,
    downloaded_at  TEXT
);

CREATE TABLE IF NOT EXISTS imports (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    source     TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    image_path TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS video_tags (
    video_id TEXT    NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id)   ON DELETE CASCADE,
    PRIMARY KEY (video_id, tag_id)
);

"""

# Indizes werden nach den Migrationen angelegt, damit sie auch neue Spalten nutzen koennen
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
CREATE INDEX IF NOT EXISTS idx_videos_import ON videos(import_id);
CREATE INDEX IF NOT EXISTS idx_video_tags_tag ON video_tags(tag_id);
"""

# Nachtraegliche Spalten fuer Datenbanken aus aelteren Versionen (Tabelle, Spalte, Definition)
MIGRATIONS: list[tuple[str, str, str]] = [
    ("videos", "attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("videos", "import_id", "TEXT REFERENCES imports(id) ON DELETE SET NULL"),
]


_NUM_RE = re.compile(r"(\d+)")
_FOLD = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss"})


def tag_sort_key(name: str) -> tuple:
    """Alphabetisch ohne Beachtung von Gross-/Kleinschreibung und Umlauten; Zahlen zuerst und
    numerisch verglichen ("2 Dinge" vor "10 Dinge" vor "Apfel")."""
    folded = name.strip().casefold().translate(_FOLD)
    parts = tuple(int(p) if p.isdigit() else p for p in _NUM_RE.split(folded) if p)
    starts_with_digit = bool(folded) and folded[0].isdigit()
    # Gemischte Typen vergleichbar machen: (0, zahl) bzw. (1, text)
    key = tuple((0, p) if isinstance(p, int) else (1, p) for p in parts)
    return (0 if starts_with_digit else 1, key)


def now_iso() -> str:
    """Zeitstempel mit Mikrosekunden, damit die Reihenfolge der Warteschlange eindeutig ist."""
    return datetime.now(UTC).isoformat()


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
            for table, column, definition in MIGRATIONS:
                existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            conn.executescript(INDEXES)

    # ---------- Videos ----------

    def create_video(
        self,
        source_url: str,
        title: str,
        tag_ids: list[int],
        import_id: str | None = None,
    ) -> dict[str, Any]:
        video_id = uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO videos (id, title, source_url, status, import_id, created_at) "
                "VALUES (?, ?, ?, 'queued', ?, ?)",
                (video_id, title, source_url, import_id, now_iso()),
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
        self,
        status: str | None = None,
        tag_id: int | None = None,
        import_id: str | None = None,
        oldest_first: bool = False,
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
        if import_id is not None:
            where.append("v.import_id = ?")
            params.append(import_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY v.created_at" + ("" if oldest_first else " DESC")
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

    def interrupted_downloads(self) -> list[dict[str, Any]]:
        """Videos, die beim letzten Lauf mitten im Download unterbrochen wurden."""
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM videos WHERE status = 'downloading'").fetchall()
            return [self._video_with_tags(conn, r) for r in rows]

    def count_by_status(self, import_id: str | None = None) -> dict[str, int]:
        sql = "SELECT status, COUNT(*) AS n FROM videos"
        params: tuple[Any, ...] = ()
        if import_id is not None:
            sql += " WHERE import_id = ?"
            params = (import_id,)
        with self.connect() as conn:
            rows = conn.execute(sql + " GROUP BY status", params)
            return {r["status"]: r["n"] for r in rows}

    def add_video_tags(self, video_id: str, tag_ids: list[int]) -> None:
        """Ergaenzt Tags, ohne bestehende zu entfernen."""
        with self.connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO video_tags (video_id, tag_id) VALUES (?, ?)",
                [(video_id, t) for t in tag_ids],
            )

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

    def create_tag(self, name: str) -> dict[str, Any]:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO tags (name, created_at) VALUES (?, ?)", (name, now_iso())
            )
            tag_id = cur.lastrowid
        return self.get_tag(tag_id)  # type: ignore[arg-type, return-value]

    def get_tag(self, tag_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tags WHERE id = ?", (tag_id,)).fetchone()
            return dict(row) if row else None

    def get_tag_by_name(self, name: str) -> dict[str, Any] | None:
        """Sucht case-insensitiv nach dem Tag-Namen."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM tags WHERE lower(name) = lower(?)", (name.strip(),)
            ).fetchone()
            return dict(row) if row else None

    def list_tags(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM tags").fetchall()
        return sorted((dict(r) for r in rows), key=lambda t: tag_sort_key(t["name"]))

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

    def thumbnails_for_tag(self, tag_id: int | None, limit: int = 4) -> list[str]:
        """Thumbnails von bis zu `limit` fertigen Videos eines Tags (None = alle Videos).

        Die Auswahl ist zufaellig, aber ueber den gespeicherten Collage-Seed stabil - sie
        aendert sich erst, wenn der Seed neu gesetzt wird (z.B. nach einem Import).
        """
        sql = (
            "SELECT v.id, v.thumbnail_path FROM videos v "
            + ("JOIN video_tags vt ON vt.video_id = v.id " if tag_id is not None else "")
            + "WHERE v.status = 'downloaded' AND v.thumbnail_path IS NOT NULL "
            + ("AND vt.tag_id = ? " if tag_id is not None else "")
        )
        params: tuple[Any, ...] = (tag_id,) if tag_id is not None else ()
        seed = self.collage_seed()
        with self.connect() as conn:
            rows = [(r["id"], r["thumbnail_path"]) for r in conn.execute(sql, params)]
        rows.sort(key=lambda r: hashlib.md5(f"{seed}:{r[0]}".encode()).hexdigest())
        return [thumb for _, thumb in rows[:limit]]

    def tags_exist(self, tag_ids: list[int]) -> bool:
        if not tag_ids:
            return True
        placeholders = ",".join("?" * len(tag_ids))
        with self.connect() as conn:
            n = conn.execute(
                f"SELECT COUNT(*) FROM tags WHERE id IN ({placeholders})", tag_ids
            ).fetchone()[0]
            return n == len(set(tag_ids))

    # ---------- Importe ----------

    def create_import(self, name: str, source: str | None) -> dict[str, Any]:
        import_id = uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO imports (id, name, source, created_at) VALUES (?, ?, ?, ?)",
                (import_id, name, source, now_iso()),
            )
        return self.get_import(import_id)  # type: ignore[return-value]

    def get_import(self, import_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id,)).fetchone()
            return dict(row) if row else None

    def list_imports(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM imports ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]

    def delete_import(self, import_id: str) -> None:
        """Entfernt nur den Import-Eintrag; Videos bleiben erhalten (import_id wird NULL)."""
        with self.connect() as conn:
            conn.execute("DELETE FROM imports WHERE id = ?", (import_id,))

    # ---------- Einstellungen ----------

    def get_setting(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def collage_seed(self) -> str:
        seed = self.get_setting("collage_seed")
        if seed is None:
            seed = self.reshuffle_collages()
        return seed

    def reshuffle_collages(self) -> str:
        """Waehlt die Collage-Bilder aller Tags neu (neuer Seed)."""
        seed = uuid.uuid4().hex
        self.set_setting("collage_seed", seed)
        return seed
