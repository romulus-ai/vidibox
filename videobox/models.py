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
    # Ohne eigenes Bild: bis zu vier Video-Thumbnails fuer eine Collage
    thumbnail_urls: list[str] = []
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
    import_id: str | None = None
    attempts: int = 0
    created_at: str
    downloaded_at: str | None


# ---------- Sonstiges ----------


class LoginRequest(BaseModel):
    pin: str


class KidsSettings(BaseModel):
    """Einstellungen, die die Kinder-UI vom Server braucht."""

    max_volume: int


class ImportRequest(BaseModel):
    """Entweder YAML-Text direkt oder eine URL zur YAML-Datei."""

    yaml: str | None = None
    url: HttpUrl | None = None


class ImportPreviewItem(BaseModel):
    url: str
    title: str | None
    tags: list[str]
    action: str  # create | add_tags | unchanged | duplicate
    status: str | None = None


class ImportPreview(BaseModel):
    name: str
    description: str | None
    new_tags: list[str]
    items: list[ImportPreviewItem]
    create: int
    add_tags: int
    unchanged: int
    duplicate: int


class ImportIssue(BaseModel):
    item: str
    message: str


class ImportReport(BaseModel):
    import_id: str
    name: str
    created: int
    tags_added: int
    unchanged: int
    tags_created: list[str]
    errors: list[ImportIssue]


class ImportProgress(BaseModel):
    id: str
    name: str
    source: str | None
    created_at: str
    total: int
    queued: int
    downloading: int
    downloaded: int
    error: int


class AdminStatus(BaseModel):
    queue_length: int
    downloading: str | None
    disk_free_bytes: int
    disk_total_bytes: int
    ytdlp_version: str
    video_counts: dict[str, int]
