import time

from videobox.db import Database
from videobox.services.downloader import DownloadWorker


def test_worker_thread_processes_queue(settings, backend):
    settings.ensure_dirs()
    db = Database(settings.db_path)
    db.init_schema()
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


def test_failed_download_cleans_partial_files(settings, backend):
    settings.ensure_dirs()
    db = Database(settings.db_path)
    db.init_schema()
    backend.fail_urls.add("https://example.org/bad")
    v = db.create_video("https://example.org/bad", "bad", [])
    (settings.videos_dir / f"{v['id']}.part").write_bytes(b"x")

    worker = DownloadWorker(db, settings, backend=backend)
    worker._process(db.get_video(v["id"]))

    assert db.get_video(v["id"])["status"] == "error"
    assert list(settings.videos_dir.iterdir()) == []


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
