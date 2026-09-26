import io
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from videobox.config import Settings
from videobox.main import create_app

PIN = "4321"


class FakeBackend:
    """Ersetzt yt-dlp: schreibt eine Dummy-Datei und ein Thumbnail."""

    def __init__(self, fail_urls: set[str] | None = None):
        self.fail_urls = fail_urls or set()
        self.calls: list[str] = []

    def download(self, url: str, video_id: str, settings: Settings) -> dict[str, Any]:
        self.calls.append(url)
        if url in self.fail_urls:
            raise RuntimeError("Video nicht verfuegbar")
        (settings.videos_dir / f"{video_id}.mp4").write_bytes(b"\x00" * 1024)
        Image.new("RGB", (32, 18), "red").save(settings.thumbs_dir / f"{video_id}.jpg")
        return {
            "title": f"Titel fuer {url}",
            "provider": "ZDF",
            "duration_s": 125,
            "file_path": f"{video_id}.mp4",
            "file_size": 1024,
            "thumbnail_path": f"{video_id}.jpg",
        }


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", admin_pin=PIN, max_volume=60)


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def app(settings: Settings, backend: FakeBackend):
    return create_app(settings, download_backend=backend, start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    r = client.post("/api/admin/login", json={"pin": PIN})
    assert r.status_code == 200
    return client


@pytest.fixture
def worker(app):
    return app.state.worker


def png_bytes(size=(200, 100)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "blue").save(buf, "PNG")
    return buf.getvalue()


def run_queue(worker) -> None:
    """Arbeitet die Warteschlange synchron ab (ohne Thread)."""
    while (video := worker.db.next_queued_video()) is not None:
        worker._process(video)
