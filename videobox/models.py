"""Pydantic-Schemas fuer API-Ein- und Ausgaben."""

from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl


class VideoStatus(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    ERROR = "error"


# ---------- Tags ----------


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    sort_order: int = 0


class TagUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    sort_order: int | None = None


class TagOut(BaseModel):
    id: int
    name: str
    sort_order: int
    has_own_image: bool
    image_url: str | None
    video_count: int


# ---------- Videos ----------


class VideoCreate(BaseModel):
    url: HttpUrl
    tag_ids: list[int] = Field(default_factory=list)


class VideoUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    tag_ids: list[int] | None = None


class VideoOut(BaseModel):
    id: str
    title: str
    source_url: str
    provider: str | None
    duration_s: int | None
    status: VideoStatus
    error_msg: str | None
    file_size: int | None
    thumbnail_url: str | None
    media_url: str | None
    tag_ids: list[int]
    created_at: str
    downloaded_at: str | None


# ---------- Sonstiges ----------


class LoginRequest(BaseModel):
    pin: str


class VolumeOut(BaseModel):
    volume: int
    max_volume: int
    available: bool


class VolumeSet(BaseModel):
    volume: int = Field(ge=0, le=100)


class AdminStatus(BaseModel):
    queue_length: int
    downloading: str | None
    disk_free_bytes: int
    disk_total_bytes: int
    ytdlp_version: str
    video_counts: dict[str, int]
