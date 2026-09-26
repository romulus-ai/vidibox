"""Admin-API: Login, Videos und Tags verwalten. Alle Routen ausser Login sind PIN-geschuetzt."""

import secrets
import shutil
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)

from videobox.api.deps import (
    SESSION_COOKIE,
    AudioDep,
    DbDep,
    SessionsDep,
    SettingsDep,
    WorkerDep,
    require_admin,
    video_to_out,
)
from videobox.models import (
    AdminStatus,
    LoginRequest,
    TagCreate,
    TagOut,
    TagUpdate,
    VideoCreate,
    VideoOut,
    VideoUpdate,
    VolumeOut,
    VolumeSet,
)
from videobox.services import tags as tag_service
from videobox.services.downloader import delete_video_files, ytdlp_version

router = APIRouter(prefix="/api/admin", tags=["admin"])
protected = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

MAX_IMAGE_BYTES = 10 * 1024 * 1024


# ---------- Login ----------


@router.post("/login")
def login(body: LoginRequest, response: Response, settings: SettingsDep, sessions: SessionsDep):
    if not secrets.compare_digest(body.pin, settings.admin_pin):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Falsche PIN")
    token = sessions.create()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response, sessions: SessionsDep):
    sessions.revoke(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/session")
def session(request: Request, sessions: SessionsDep):
    return {"authenticated": sessions.is_valid(request.cookies.get(SESSION_COOKIE))}


# ---------- Status ----------


@protected.get("/status", response_model=AdminStatus)
def admin_status(db: DbDep, settings: SettingsDep, worker: WorkerDep):
    usage = shutil.disk_usage(settings.data_dir)
    counts = db.count_by_status()
    return AdminStatus(
        queue_length=counts.get("queued", 0),
        downloading=worker.current_video_id,
        disk_free_bytes=usage.free,
        disk_total_bytes=usage.total,
        ytdlp_version=ytdlp_version(),
        video_counts=counts,
    )


@protected.get("/volume", response_model=VolumeOut)
def get_volume(audio: AudioDep):
    return VolumeOut(volume=audio.get(), max_volume=audio.max_volume, available=audio.available)


@protected.put("/volume", response_model=VolumeOut)
def set_volume(body: VolumeSet, audio: AudioDep):
    v = audio.set(body.volume)
    return VolumeOut(volume=v, max_volume=audio.max_volume, available=audio.available)


# ---------- Videos ----------


@protected.get("/videos", response_model=list[VideoOut])
def list_videos(db: DbDep, status_filter: Annotated[str | None, Query(alias="status")] = None):
    return [video_to_out(v) for v in db.list_videos(status=status_filter)]


@protected.post("/videos", response_model=VideoOut, status_code=status.HTTP_201_CREATED)
def create_video(body: VideoCreate, db: DbDep, worker: WorkerDep):
    url = str(body.url)
    if db.get_video_by_url(url):
        raise HTTPException(status.HTTP_409_CONFLICT, "Video mit dieser URL existiert bereits")
    if not db.tags_exist(body.tag_ids):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unbekannter Tag")
    video = db.create_video(source_url=url, title=url, tag_ids=body.tag_ids)
    worker.notify()
    return video_to_out(video)


@protected.get("/videos/{video_id}", response_model=VideoOut)
def get_video(video_id: str, db: DbDep):
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video nicht gefunden")
    return video_to_out(video)


@protected.put("/videos/{video_id}", response_model=VideoOut)
def update_video(video_id: str, body: VideoUpdate, db: DbDep):
    if not db.get_video(video_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video nicht gefunden")
    fields = body.model_dump(exclude_none=True)
    if "tag_ids" in fields and not db.tags_exist(fields["tag_ids"]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unbekannter Tag")
    db.update_video(video_id, **fields)
    return video_to_out(db.get_video(video_id))  # type: ignore[arg-type]


@protected.post("/videos/{video_id}/retry", response_model=VideoOut)
def retry_video(video_id: str, db: DbDep, worker: WorkerDep):
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video nicht gefunden")
    if video["status"] != "error":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nur fehlgeschlagene Videos")
    db.update_video(video_id, status="queued", error_msg=None)
    worker.notify()
    return video_to_out(db.get_video(video_id))  # type: ignore[arg-type]


@protected.delete("/videos/{video_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_video(video_id: str, db: DbDep, settings: SettingsDep):
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video nicht gefunden")
    if video["status"] == "downloading":
        raise HTTPException(status.HTTP_409_CONFLICT, "Download laeuft gerade")
    db.delete_video(video_id)
    delete_video_files(video, settings)


# ---------- Tags ----------


def _tag_out(db, tag) -> dict:
    return tag_service.tag_to_out(db, tag, db.tag_video_counts())


@protected.get("/tags", response_model=list[TagOut])
def list_tags(db: DbDep):
    counts = db.tag_video_counts()
    return [tag_service.tag_to_out(db, t, counts) for t in db.list_tags()]


@protected.post("/tags", response_model=TagOut, status_code=status.HTTP_201_CREATED)
def create_tag(body: TagCreate, db: DbDep):
    name = body.name.strip()
    if any(t["name"].lower() == name.lower() for t in db.list_tags()):
        raise HTTPException(status.HTTP_409_CONFLICT, "Tag existiert bereits")
    tag = db.create_tag(name, body.sort_order)
    return _tag_out(db, tag)


@protected.put("/tags/{tag_id}", response_model=TagOut)
def update_tag(tag_id: int, body: TagUpdate, db: DbDep):
    if not db.get_tag(tag_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tag nicht gefunden")
    fields = body.model_dump(exclude_none=True)
    if "name" in fields:
        fields["name"] = fields["name"].strip()
        for t in db.list_tags():
            if t["id"] != tag_id and t["name"].lower() == fields["name"].lower():
                raise HTTPException(status.HTTP_409_CONFLICT, "Tag existiert bereits")
    db.update_tag(tag_id, **fields)
    return _tag_out(db, db.get_tag(tag_id))


@protected.delete("/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tag(tag_id: int, db: DbDep, settings: SettingsDep):
    tag = db.get_tag(tag_id)
    if not tag:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tag nicht gefunden")
    tag_service.remove_tag_image(db, settings, tag)
    db.delete_tag(tag_id)


@protected.post("/tags/{tag_id}/image", response_model=TagOut)
async def upload_tag_image(tag_id: int, file: UploadFile, db: DbDep, settings: SettingsDep):
    if not db.get_tag(tag_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tag nicht gefunden")
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Bild zu gross (max 10 MB)")
    try:
        tag_service.save_tag_image(db, settings, tag_id, data)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return _tag_out(db, db.get_tag(tag_id))


@protected.delete("/tags/{tag_id}/image", response_model=TagOut)
def delete_tag_image(tag_id: int, db: DbDep, settings: SettingsDep):
    tag = db.get_tag(tag_id)
    if not tag:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tag nicht gefunden")
    tag_service.remove_tag_image(db, settings, tag)
    return _tag_out(db, db.get_tag(tag_id))
