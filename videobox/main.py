"""FastAPI-Anwendung: Router, statische Dateien, Lebenszyklus."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from videobox import __version__
from videobox.api import admin, kids
from videobox.api.deps import SessionStore
from videobox.config import Settings, get_settings
from videobox.db import Database
from videobox.services.downloader import DownloadWorker, YtDlpLike

STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    settings: Settings | None = None,
    download_backend: YtDlpLike | None = None,
    start_worker: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_dirs()

    db = Database(settings.db_path)
    db.init_schema()
    worker = DownloadWorker(db, settings, backend=download_backend)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_worker:
            worker.start()
        yield
        worker.stop()

    app = FastAPI(title="Videobox", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.worker = worker
    app.state.sessions = SessionStore(ttl_seconds=settings.session_hours * 3600)

    app.include_router(kids.router)
    app.include_router(admin.router)
    app.include_router(admin.protected)

    @app.get("/api/health", tags=["system"])
    def health():
        return {"status": "ok", "version": __version__}

    # Medien (StaticFiles unterstuetzt HTTP-Range fuer Video-Seeking)
    app.mount("/media/videos", StaticFiles(directory=settings.videos_dir), name="videos")
    app.mount("/media/thumbs", StaticFiles(directory=settings.thumbs_dir), name="thumbs")
    app.mount("/media/tags", StaticFiles(directory=settings.tags_dir), name="tag-images")

    # UIs
    @app.get("/admin", include_in_schema=False)
    def admin_ui():
        return FileResponse(STATIC_DIR / "admin" / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.mount("/", StaticFiles(directory=STATIC_DIR / "kids", html=True), name="kids-ui")

    return app


def run() -> None:
    import uvicorn

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    run()
