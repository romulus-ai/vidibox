"""Hintergrund-Downloader: holt Videos nacheinander per yt-dlp.

Ein Worker-Thread arbeitet die Warteschlange (Status 'queued' in der DB) ab.
Pro Video: Metadaten (Titel, Dauer, Anbieter), Datei als MP4/H.264 bis zur
konfigurierten Aufloesung sowie ein JPEG-Thumbnail.
"""

import logging
import threading
from pathlib import Path
from typing import Any, Protocol

from videobox.config import Settings
from videobox.db import Database, now_iso
from videobox.services.sources import normalize_source_url

log = logging.getLogger(__name__)


class YtDlpLike(Protocol):
    """Minimales Interface, damit yt-dlp in Tests ersetzt werden kann."""

    def download(self, url: str, video_id: str, settings: Settings) -> dict[str, Any]: ...


class YtDlpDownloader:
    """Echter Download ueber die yt-dlp-Python-API."""

    def download(self, url: str, video_id: str, settings: Settings) -> dict[str, Any]:
        import yt_dlp  # spaeter Import: haelt Tests ohne yt-dlp lauffaehig

        h = settings.max_resolution
        fmt = (
            f"bv*[vcodec^=avc1][height<={h}]+ba[acodec^=mp4a]"
            f"/b[ext=mp4][height<={h}]"
            f"/bv*[height<={h}]+ba/b"
        )
        outtmpl = str(settings.videos_dir / f"{video_id}.%(ext)s")
        opts: dict[str, Any] = {
            "format": fmt,
            "merge_output_format": "mp4",
            "outtmpl": {"default": outtmpl, "thumbnail": str(settings.thumbs_dir / video_id)},
            "writethumbnail": True,
            "postprocessors": [
                {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
                {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
            ],
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "retries": 3,
            "restrictfilenames": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise RuntimeError("yt-dlp lieferte keine Informationen")
            # Bei Playlists (sollte durch noplaylist nicht passieren) ersten Eintrag nehmen
            if "entries" in info and info["entries"]:
                info = info["entries"][0]

        file_path = self._find_file(settings.videos_dir, video_id, ("mp4", "mkv", "webm"))
        if file_path is None:
            raise RuntimeError("Videodatei nach Download nicht gefunden")
        thumb_path = self._find_file(settings.thumbs_dir, video_id, ("jpg", "jpeg", "png", "webp"))
        if thumb_path is not None:
            thumb_path = shrink_thumbnail(thumb_path)

        return {
            "title": info.get("title") or url,
            "provider": info.get("extractor_key") or info.get("extractor"),
            "duration_s": int(info["duration"]) if info.get("duration") else None,
            "file_path": file_path.name,
            "file_size": file_path.stat().st_size,
            "thumbnail_path": thumb_path.name if thumb_path else None,
        }

    @staticmethod
    def _find_file(directory: Path, stem: str, exts: tuple[str, ...]) -> Path | None:
        for ext in exts:
            p = directory / f"{stem}.{ext}"
            if p.exists():
                return p
        return None


THUMB_MAX_WIDTH = 640


def shrink_thumbnail(path: Path) -> Path:
    """Skaliert das Thumbnail auf Kachelgroesse und speichert es als JPEG."""
    from PIL import Image

    target = path.with_suffix(".jpg")
    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            if img.width > THUMB_MAX_WIDTH:
                ratio = THUMB_MAX_WIDTH / img.width
                img = img.resize(
                    (THUMB_MAX_WIDTH, round(img.height * ratio)), Image.Resampling.LANCZOS
                )
            img.save(target, "JPEG", quality=82, optimize=True)
        if target != path:
            path.unlink(missing_ok=True)
        return target
    except Exception:  # noqa: BLE001
        log.warning("Thumbnail konnte nicht verkleinert werden: %s", path, exc_info=True)
        return path


class DownloadWorker:
    """Steuert den Worker-Thread und die Abarbeitung der Warteschlange."""

    def __init__(self, db: Database, settings: Settings, backend: YtDlpLike | None = None):
        self.db = db
        self.settings = settings
        self.backend = backend or YtDlpDownloader()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.current_video_id: str | None = None

    # ---------- Lebenszyklus ----------

    def start(self) -> None:
        self.recover_interrupted()
        self._thread = threading.Thread(target=self._run, name="downloader", daemon=True)
        self._thread.start()
        self.notify()

    def recover_interrupted(self) -> None:
        """Stellt beim Start unterbrochene Downloads zurueck in die Warteschlange.

        Teildateien werden entfernt, damit der Download sauber neu beginnt. Wird ein Video
        wiederholt unterbrochen (z.B. Absturz bei genau diesem Video), landet es nach
        max_attempts Versuchen im Fehlerstatus statt endlos zu kreisen.
        """
        for video in self.db.interrupted_downloads():
            self._cleanup_partial(video["id"])
            attempts = int(video.get("attempts") or 0)
            if attempts >= self.settings.max_attempts:
                log.warning(
                    "Download nach %d Versuchen aufgegeben: %s", attempts, video["source_url"]
                )
                self.db.update_video(
                    video["id"],
                    status="error",
                    error_msg=f"Nach {attempts} Versuchen abgebrochen (Download wurde wiederholt "
                    "unterbrochen)",
                )
            else:
                log.info("Unterbrochenen Download erneut eingereiht: %s", video["source_url"])
                self.db.update_video(video["id"], status="queued")

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def notify(self) -> None:
        """Weckt den Worker, weil neue Eintraege in der Warteschlange sind."""
        self._wake.set()

    # ---------- Arbeit ----------

    def _run(self) -> None:
        while not self._stop.is_set():
            video = self.db.next_queued_video()
            if video is None:
                self._wake.wait(timeout=30)
                self._wake.clear()
                continue
            self._process(video)

    def _process(self, video: dict[str, Any]) -> None:
        video_id = video["id"]
        self.current_video_id = video_id
        self.db.update_video(
            video_id,
            status="downloading",
            error_msg=None,
            attempts=int(video.get("attempts") or 0) + 1,
        )
        log.info("Download gestartet: %s", video["source_url"])
        try:
            url = normalize_source_url(video["source_url"])
            result = self.backend.download(url, video_id, self.settings)
            # Ein vom Nutzer bzw. Import vorgegebener Titel hat Vorrang vor dem yt-dlp-Titel
            if video["title"] and video["title"] != video["source_url"]:
                result.pop("title", None)
            self.db.update_video(
                video_id,
                status="downloaded",
                downloaded_at=now_iso(),
                error_msg=None,
                **result,
            )
            log.info("Download fertig: %s", result.get("title"))
        except Exception as exc:  # noqa: BLE001 - alles als Fehlerstatus festhalten
            log.exception("Download fehlgeschlagen: %s", video["source_url"])
            self._cleanup_partial(video_id)
            self.db.update_video(video_id, status="error", error_msg=str(exc)[:500])
        finally:
            self.current_video_id = None

    def _cleanup_partial(self, video_id: str) -> None:
        for d in (self.settings.videos_dir, self.settings.thumbs_dir):
            for p in d.glob(f"{video_id}.*"):
                try:
                    p.unlink()
                except OSError:
                    pass


def delete_video_files(video: dict[str, Any], settings: Settings) -> None:
    """Loescht Videodatei und Thumbnail eines Videos (falls vorhanden)."""
    for d in (settings.videos_dir, settings.thumbs_dir):
        for p in d.glob(f"{video['id']}.*"):
            try:
                p.unlink()
            except OSError:
                log.warning("Konnte %s nicht loeschen", p)


def ytdlp_version() -> str:
    try:
        from yt_dlp.version import __version__

        return __version__
    except Exception:  # noqa: BLE001
        return "unbekannt"
