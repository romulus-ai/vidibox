"""Tag-Logik: Bild-Aufloesung und Bild-Upload."""

import io
from typing import Any

from PIL import Image

from videobox.config import Settings
from videobox.db import Database

TAG_IMAGE_SIZE = 512
ALL_TAG_ID = 0  # virtuelle "Alle"-Kachel in der Kinder-UI


COLLAGE_SIZE = 4


def resolve_tag_images(db: Database, tag_id: int | None, image_path: str | None) -> dict[str, Any]:
    """Bildinformationen fuer eine Tag-Kachel.

    Eigenes Bild -> image_url gesetzt, thumbnail_urls leer.
    Sonst -> bis zu vier Thumbnails der ersten Videos (Collage), image_url = erstes davon.
    Ohne Videos -> beides leer (Platzhalter im UI).
    """
    if image_path:
        return {"image_url": f"/media/tags/{image_path}", "thumbnail_urls": []}
    thumbs = [f"/media/thumbs/{t}" for t in db.thumbnails_for_tag(tag_id, COLLAGE_SIZE)]
    return {"image_url": thumbs[0] if thumbs else None, "thumbnail_urls": thumbs}


def tag_to_out(db: Database, tag: dict[str, Any], counts: dict[int, int]) -> dict[str, Any]:
    return {
        "id": tag["id"],
        "name": tag["name"],
        "sort_order": tag["sort_order"],
        "has_own_image": bool(tag.get("image_path")),
        "video_count": counts.get(tag["id"], 0),
        **resolve_tag_images(db, tag["id"], tag.get("image_path")),
    }


def save_tag_image(db: Database, settings: Settings, tag_id: int, data: bytes) -> str:
    """Skaliert das hochgeladene Bild auf ein Quadrat und speichert es als JPEG."""
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Ungueltige Bilddatei") from exc

    img = img.convert("RGB")
    # Mittig quadratisch zuschneiden, dann skalieren
    w, h = img.size
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    img = img.resize((TAG_IMAGE_SIZE, TAG_IMAGE_SIZE), Image.Resampling.LANCZOS)

    filename = f"{tag_id}.jpg"
    img.save(settings.tags_dir / filename, "JPEG", quality=85)
    db.update_tag(tag_id, image_path=filename)
    return filename


def remove_tag_image(db: Database, settings: Settings, tag: dict[str, Any]) -> None:
    if tag.get("image_path"):
        p = settings.tags_dir / tag["image_path"]
        if p.exists():
            p.unlink()
    db.update_tag(tag["id"], image_path=None)
