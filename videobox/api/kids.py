"""Kinder-API: nur lesend, liefert ausschliesslich fertig heruntergeladene Videos."""

from fastapi import APIRouter, HTTPException, status

from videobox.api.deps import DbDep, SettingsDep, video_to_out
from videobox.models import KidsSettings, TagOut, VideoOut
from videobox.services import tags as tag_service

router = APIRouter(prefix="/api", tags=["kids"])


def _all_tag(db: DbDep) -> dict:
    counts = db.count_by_status()
    return {
        "id": tag_service.ALL_TAG_ID,
        "name": "Alle",
        "sort_order": -1,
        "has_own_image": False,
        "video_count": counts.get("downloaded", 0),
        **tag_service.resolve_tag_images(db, None, None),
    }


@router.get("/tags", response_model=list[TagOut])
def list_tags(db: DbDep):
    counts = db.tag_video_counts()
    tags = [tag_service.tag_to_out(db, t, counts) for t in db.list_tags()]
    return [_all_tag(db), *tags]


@router.get("/tags/{tag_id}/videos", response_model=list[VideoOut])
def tag_videos(tag_id: int, db: DbDep):
    if tag_id == tag_service.ALL_TAG_ID:
        videos = db.list_videos(status="downloaded")
    else:
        if not db.get_tag(tag_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Tag nicht gefunden")
        videos = db.list_videos(status="downloaded", tag_id=tag_id)
    return [video_to_out(v) for v in videos]


@router.get("/videos/{video_id}", response_model=VideoOut)
def get_video(video_id: str, db: DbDep):
    video = db.get_video(video_id)
    if not video or video["status"] != "downloaded":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video nicht gefunden")
    return video_to_out(video)


@router.get("/settings", response_model=KidsSettings)
def kids_settings(settings: SettingsDep):
    return KidsSettings(max_volume=settings.max_volume)
