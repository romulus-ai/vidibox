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
    DbDep,
    SessionsDep,
    SettingsDep,
    WorkerDep,
    require_admin,
    video_to_out,
)
from videobox.models import (
    AdminStatus,
    ImportPreview,
    ImportProgress,
    ImportReport,
    ImportRequest,
    LoginRequest,
    TagCreate,
    TagOut,
    TagUpdate,
    VideoCreate,
    VideoOut,
    VideoUpdate,
)
from videobox.services import importer
from videobox.services import tags as tag_service
from videobox.services.downloader import delete_video_files, ytdlp_version
from videobox.services.sources import normalize_source_url

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


# ---------- Videos ----------


@protected.get("/videos", response_model=list[VideoOut])
def list_videos(db: DbDep, status_filter: Annotated[str | None, Query(alias="status")] = None):
    return [video_to_out(v) for v in db.list_videos(status=status_filter)]


@protected.post("/videos", response_model=VideoOut, status_code=status.HTTP_201_CREATED)
def create_video(body: VideoCreate, db: DbDep, worker: WorkerDep):
    url = normalize_source_url(str(body.url))
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
    db.update_video(video_id, status="queued", error_msg=None, attempts=0)
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
    tag = db.create_tag(name)
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


# ---------- Import ----------


def _load_list(body: ImportRequest) -> tuple[importer.ImportList, str | None]:
    """Liest die Liste aus YAML-Text oder von einer URL; liefert (Liste, Quelle)."""
    try:
        if body.url:
            url = str(body.url)
            return importer.parse_list(importer.fetch_list(url)), url
        if body.yaml and body.yaml.strip():
            return importer.parse_list(body.yaml), None
    except importer.ImportError_ as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bitte YAML-Text oder URL angeben")


async def _load_list_from_upload(file: UploadFile) -> tuple[importer.ImportList, str | None]:
    data = await file.read(importer.MAX_LIST_BYTES + 1)
    if len(data) > importer.MAX_LIST_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Liste zu gross (max 1 MB)")
    try:
        return importer.parse_list(data.decode("utf-8", errors="replace")), file.filename
    except importer.ImportError_ as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@protected.post("/import/preview", response_model=ImportPreview)
def import_preview(body: ImportRequest, db: DbDep):
    lst, _ = _load_list(body)
    return importer.preview(db, lst)


@protected.post("/import/preview/file", response_model=ImportPreview)
async def import_preview_file(file: UploadFile, db: DbDep):
    lst, _ = await _load_list_from_upload(file)
    return importer.preview(db, lst)


@protected.post("/import", response_model=ImportReport, status_code=status.HTTP_201_CREATED)
def import_run(body: ImportRequest, db: DbDep, settings: SettingsDep, worker: WorkerDep):
    lst, source = _load_list(body)
    report = importer.run_import(db, settings, lst, source)
    worker.notify()
    return report


@protected.post("/import/file", response_model=ImportReport, status_code=status.HTTP_201_CREATED)
async def import_run_file(file: UploadFile, db: DbDep, settings: SettingsDep, worker: WorkerDep):
    lst, source = await _load_list_from_upload(file)
    report = importer.run_import(db, settings, lst, source)
    worker.notify()
    return report


@protected.get("/imports", response_model=list[ImportProgress])
def list_imports(db: DbDep):
    return [importer.import_progress(db, imp) for imp in db.list_imports()]


@protected.get("/imports/{import_id}/videos", response_model=list[VideoOut])
def import_videos(import_id: str, db: DbDep):
    if not db.get_import(import_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import nicht gefunden")
    return [video_to_out(v) for v in db.list_videos(import_id=import_id, oldest_first=True)]


@protected.delete("/imports/{import_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_import(import_id: str, db: DbDep):
    """Entfernt den Import aus der Uebersicht; Videos und Tags bleiben erhalten."""
    if not db.get_import(import_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import nicht gefunden")
    db.delete_import(import_id)
