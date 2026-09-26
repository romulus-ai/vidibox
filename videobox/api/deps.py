"""Gemeinsame FastAPI-Dependencies und Admin-Session-Verwaltung."""

import secrets
import time
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status

from videobox.config import Settings
from videobox.db import Database
from videobox.services.audio import AudioControl
from videobox.services.downloader import DownloadWorker

SESSION_COOKIE = "videobox_admin"


class SessionStore:
    """Einfache In-Memory-Sessions fuer den Admin-Login."""

    def __init__(self, ttl_seconds: int):
        self.ttl = ttl_seconds
        self._tokens: dict[str, float] = {}

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        self._tokens[token] = time.time() + self.ttl
        return token

    def is_valid(self, token: str | None) -> bool:
        if not token:
            return False
        exp = self._tokens.get(token)
        if exp is None:
            return False
        if exp < time.time():
            self._tokens.pop(token, None)
            return False
        return True

    def revoke(self, token: str | None) -> None:
        if token:
            self._tokens.pop(token, None)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_worker(request: Request) -> DownloadWorker:
    return request.app.state.worker


def get_audio(request: Request) -> AudioControl:
    return request.app.state.audio


def get_sessions(request: Request) -> SessionStore:
    return request.app.state.sessions


def require_admin(
    request: Request, sessions: Annotated[SessionStore, Depends(get_sessions)]
) -> None:
    if not sessions.is_valid(request.cookies.get(SESSION_COOKIE)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht angemeldet")


SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[Database, Depends(get_db)]
WorkerDep = Annotated[DownloadWorker, Depends(get_worker)]
AudioDep = Annotated[AudioControl, Depends(get_audio)]
SessionsDep = Annotated[SessionStore, Depends(get_sessions)]


def video_to_out(video: dict[str, Any]) -> dict[str, Any]:
    downloaded = video["status"] == "downloaded"
    return {
        "id": video["id"],
        "title": video["title"],
        "source_url": video["source_url"],
        "provider": video.get("provider"),
        "duration_s": video.get("duration_s"),
        "status": video["status"],
        "error_msg": video.get("error_msg"),
        "file_size": video.get("file_size"),
        "thumbnail_url": (
            f"/media/thumbs/{video['thumbnail_path']}" if video.get("thumbnail_path") else None
        ),
        "media_url": (
            f"/media/videos/{video['file_path']}" if downloaded and video.get("file_path") else None
        ),
        "tag_ids": video.get("tag_ids", []),
        "created_at": video["created_at"],
        "downloaded_at": video.get("downloaded_at"),
    }
