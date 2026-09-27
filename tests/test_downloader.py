import time

from videobox.db import Database
from videobox.services.downloader import DownloadWorker


def _db(settings) -> Database:
    settings.ensure_dirs()
    db = Database(settings.db_path)
    db.init_schema()
    return db


def test_worker_thread_processes_queue(settings, backend):
    db = _db(settings)
    v1 = db.create_video("https://example.org/1", "1", [])
    db.update_video(v1["id"], status="downloading")  # simuliert Abbruch beim letzten Lauf

    worker = DownloadWorker(db, settings, backend=backend)
    worker.start()
    try:
        v2 = db.create_video("https://example.org/2", "2", [])
        worker.notify()
        deadline = time.time() + 5
        while time.time() < deadline:
            statuses = {db.get_video(v["id"])["status"] for v in (v1, v2)}
            if statuses == {"downloaded"}:
                break
            time.sleep(0.05)
        assert statuses == {"downloaded"}
        # Reihenfolge: aeltestes zuerst
        assert backend.calls == ["https://example.org/1", "https://example.org/2"]
    finally:
        worker.stop()
    assert worker.current_video_id is None


def test_queue_order_is_stable_for_fast_inserts(settings, backend):
    db = _db(settings)
    ids = [db.create_video(f"https://example.org/{i}", str(i), [])["id"] for i in range(20)]
    worker = DownloadWorker(db, settings, backend=backend)
    while (v := db.next_queued_video()) is not None:
        worker._process(v)
    assert backend.calls == [f"https://example.org/{i}" for i in range(20)]
    assert all(db.get_video(i)["status"] == "downloaded" for i in ids)


def test_failed_download_cleans_partial_files(settings, backend):
    db = _db(settings)
    backend.fail_urls.add("https://example.org/bad")
    v = db.create_video("https://example.org/bad", "bad", [])
    (settings.videos_dir / f"{v['id']}.part").write_bytes(b"x")

    worker = DownloadWorker(db, settings, backend=backend)
    worker._process(db.get_video(v["id"]))

    assert db.get_video(v["id"])["status"] == "error"
    assert list(settings.videos_dir.iterdir()) == []


def test_recovery_requeues_and_cleans_partial_files(settings, backend):
    """Nach einem harten Neustart: 'downloading' -> 'queued', Teildateien weg."""
    db = _db(settings)
    v = db.create_video("https://example.org/1", "1", [])
    db.update_video(v["id"], status="downloading", attempts=1)
    (settings.videos_dir / f"{v['id']}.f137.mp4.part").write_bytes(b"x")
    (settings.thumbs_dir / f"{v['id']}.webp").write_bytes(b"x")

    DownloadWorker(db, settings, backend=backend).recover_interrupted()

    assert db.get_video(v["id"])["status"] == "queued"
    assert list(settings.videos_dir.iterdir()) == []
    assert list(settings.thumbs_dir.iterdir()) == []


def test_recovery_gives_up_after_max_attempts(settings, backend):
    settings.max_attempts = 3
    db = _db(settings)
    v = db.create_video("https://example.org/crash", "crash", [])
    worker = DownloadWorker(db, settings, backend=backend)

    # Drei Laeufe, die jeweils mitten im Download "abstuerzen"
    for expected_attempts in (1, 2, 3):
        worker.recover_interrupted()
        assert db.get_video(v["id"])["status"] == "queued"
        db.update_video(v["id"], status="downloading", attempts=expected_attempts)

    worker.recover_interrupted()
    video = db.get_video(v["id"])
    assert video["status"] == "error"
    assert "3 Versuchen" in video["error_msg"]


def test_process_increments_attempts_and_keeps_custom_title(settings, backend):
    db = _db(settings)
    custom = db.create_video("https://example.org/1", "Mein Titel", [])
    plain = db.create_video("https://example.org/2", "https://example.org/2", [])
    worker = DownloadWorker(db, settings, backend=backend)
    worker._process(db.get_video(custom["id"]))
    worker._process(db.get_video(plain["id"]))

    assert db.get_video(custom["id"])["title"] == "Mein Titel"
    assert db.get_video(plain["id"])["title"].startswith("Titel fuer")
    assert db.get_video(custom["id"])["attempts"] == 1


def test_shrink_thumbnail(tmp_path):
    from PIL import Image

    from videobox.services.downloader import shrink_thumbnail

    src = tmp_path / "t.webp"
    Image.new("RGB", (1920, 1080), "green").save(src, "WEBP")
    out = shrink_thumbnail(src)
    assert out.suffix == ".jpg" and not src.exists()
    assert Image.open(out).size == (640, 360)


def test_normalize_source_url():
    from videobox.services.sources import normalize_source_url

    assert (
        normalize_source_url("https://schule.zdf.de/video/abc-100?x=1#top")
        == "https://www.zdf.de/video/abc-100"
    )
    assert (
        normalize_source_url("https://www.youtube.com/watch?v=abc#t=1")
        == "https://www.youtube.com/watch?v=abc"
    )
    assert normalize_source_url("https://www.kika.de/x/video-1") == "https://www.kika.de/x/video-1"


def test_schema_migration_adds_columns(settings):
    """Eine DB aus der ersten Version (ohne attempts/import_id) wird beim Start migriert."""
    import sqlite3

    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path)
    conn.executescript(
        "CREATE TABLE videos (id TEXT PRIMARY KEY, title TEXT NOT NULL, source_url TEXT NOT NULL "
        "UNIQUE, provider TEXT, duration_s INTEGER, thumbnail_path TEXT, file_path TEXT, "
        "file_size INTEGER, status TEXT NOT NULL DEFAULT 'queued', error_msg TEXT, "
        "created_at TEXT NOT NULL, downloaded_at TEXT);"
        "INSERT INTO videos (id, title, source_url, created_at) VALUES ('a', 'A', 'u', 't');"
    )
    conn.commit()
    conn.close()

    db = Database(settings.db_path)
    db.init_schema()
    v = db.get_video("a")
    assert v["attempts"] == 0 and v["import_id"] is None
    db.init_schema()  # idempotent
