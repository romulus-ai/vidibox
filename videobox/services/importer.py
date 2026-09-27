"""Import vorkuratierter Videolisten im YAML-Format.

Format (alle Felder ausser `videos[].url` optional):

    name: ZDF Schule - Biologie
    description: Kurze Beschreibung
    tags:
      - name: Biologie
        image: https://example.org/biologie.jpg
    default_tags: [Biologie]
    videos:
      - url: https://schule.zdf.de/video/abc-100
        tags: [Koerper]
        title: Eigener Titel
      - https://schule.zdf.de/video/def-100      # Kurzform

Der Import legt Tags an (case-insensitiv abgeglichen), stellt neue Videos in die
Download-Warteschlange und ergaenzt bei bereits vorhandenen Videos nur die Tags.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from videobox.config import Settings
from videobox.db import Database
from videobox.services import tags as tag_service
from videobox.services.sources import normalize_source_url

log = logging.getLogger(__name__)

MAX_LIST_BYTES = 1024 * 1024
FETCH_TIMEOUT = 15.0


class ImportError_(Exception):
    """Fehler beim Parsen oder Laden einer Liste (fuer den Nutzer lesbar)."""


# ---------- Schema ----------


class TagDef(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    image: str | None = None
    sort_order: int | None = None

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class VideoDef(BaseModel):
    url: str = Field(min_length=8)
    title: str | None = Field(default=None, max_length=200)
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _shorthand(cls, data: Any) -> Any:
        # Kurzform: nur die URL als String
        if isinstance(data, str):
            return {"url": data}
        return data

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL muss mit http:// oder https:// beginnen")
        return v

    @field_validator("tags", mode="before")
    @classmethod
    def _tags_list(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        return v

    @field_validator("title")
    @classmethod
    def _title(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v or None


class ImportList(BaseModel):
    name: str = Field(default="Import", min_length=1, max_length=200)
    description: str | None = None
    tags: list[TagDef] = Field(default_factory=list)
    default_tags: list[str] = Field(default_factory=list)
    videos: list[VideoDef] = Field(min_length=1)

    @field_validator("default_tags", mode="before")
    @classmethod
    def _tags_list(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        return v


# ---------- Parsen / Laden ----------


def parse_list(text: str) -> ImportList:
    if len(text.encode("utf-8")) > MAX_LIST_BYTES:
        raise ImportError_("Liste ist zu gross (max. 1 MB)")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (Zeile {mark.line + 1})" if mark else ""
        raise ImportError_(f"YAML-Fehler{where}: {getattr(exc, 'problem', exc)}") from exc
    if not isinstance(data, dict):
        raise ImportError_("Die Liste muss ein YAML-Objekt mit dem Schluessel 'videos' sein")
    try:
        return ImportList.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"]) or "Liste"
        raise ImportError_(f"Ungueltiges Feld '{loc}': {first['msg']}") from exc


def fetch_list(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        raise ImportError_("Nur http(s)-URLs werden unterstuetzt")
    try:
        with httpx.Client(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                chunks: list[bytes] = []
                size = 0
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > MAX_LIST_BYTES:
                        raise ImportError_("Liste ist zu gross (max. 1 MB)")
                    chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise ImportError_(f"Liste konnte nicht geladen werden: {exc}") from exc
    return b"".join(chunks).decode("utf-8", errors="replace")


# ---------- Vorschau / Ausfuehrung ----------


def _video_tag_names(lst: ImportList, video: VideoDef) -> list[str]:
    seen: dict[str, str] = {}
    for name in [*lst.default_tags, *video.tags]:
        key = name.strip().lower()
        if key and key not in seen:
            seen[key] = name.strip()
    return list(seen.values())


def preview(db: Database, lst: ImportList) -> dict[str, Any]:
    """Zeigt, was ein Import anlegen bzw. aendern wuerde, ohne zu schreiben."""
    existing_tags = {t["name"].lower(): t for t in db.list_tags()}
    new_tags: dict[str, str] = {}

    def check_tag(name: str) -> None:
        if name.lower() not in existing_tags and name.lower() not in new_tags:
            new_tags[name.lower()] = name

    for t in lst.tags:
        check_tag(t.name)

    items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for v in lst.videos:
        names = _video_tag_names(lst, v)
        url = normalize_source_url(v.url)
        if url in seen_urls:
            items.append({"url": v.url, "title": v.title, "tags": names, "action": "duplicate"})
            continue
        seen_urls.add(url)
        for n in names:
            check_tag(n)
        existing = db.get_video_by_url(url)
        if existing:
            have = set(existing["tag_ids"])
            missing = [
                n
                for n in names
                if n.lower() not in existing_tags or existing_tags[n.lower()]["id"] not in have
            ]
            items.append(
                {
                    "url": v.url,
                    "title": existing["title"],
                    "tags": names,
                    "action": "add_tags" if missing else "unchanged",
                    "status": existing["status"],
                }
            )
        else:
            items.append({"url": v.url, "title": v.title, "tags": names, "action": "create"})

    counts = {
        k: sum(1 for i in items if i["action"] == k)
        for k in ("create", "add_tags", "unchanged", "duplicate")
    }
    return {
        "name": lst.name,
        "description": lst.description,
        "new_tags": list(new_tags.values()),
        "items": items,
        **counts,
    }


def run_import(
    db: Database, settings: Settings, lst: ImportList, source: str | None
) -> dict[str, Any]:
    """Fuehrt den Import aus: Tags anlegen, Videos einreihen, Tags ergaenzen."""
    imp = db.create_import(lst.name, source)
    report: dict[str, Any] = {
        "import_id": imp["id"],
        "name": lst.name,
        "created": 0,
        "tags_added": 0,
        "unchanged": 0,
        "tags_created": [],
        "errors": [],
    }

    tag_ids: dict[str, int] = {}

    def ensure_tag(name: str) -> int:
        key = name.lower()
        if key in tag_ids:
            return tag_ids[key]
        tag = db.get_tag_by_name(name)
        if tag is None:
            sort_order = max([t["sort_order"] for t in db.list_tags()] or [-1]) + 1
            tag = db.create_tag(name, sort_order)
            report["tags_created"].append(name)
        tag_ids[key] = tag["id"]
        return tag["id"]

    # Vordefinierte Tags inkl. Bild
    for tdef in lst.tags:
        try:
            tid = ensure_tag(tdef.name)
            if tdef.sort_order is not None:
                db.update_tag(tid, sort_order=tdef.sort_order)
            tag = db.get_tag(tid)
            if tdef.image and tag and not tag.get("image_path"):
                _fetch_tag_image(db, settings, tid, tdef.image)
        except ImportError_ as exc:
            report["errors"].append({"item": f"Tag '{tdef.name}'", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            log.exception("Tag-Import fehlgeschlagen: %s", tdef.name)
            report["errors"].append({"item": f"Tag '{tdef.name}'", "message": str(exc)[:300]})

    # Videos
    seen_urls: set[str] = set()
    for v in lst.videos:
        try:
            url = normalize_source_url(v.url)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            ids = [ensure_tag(n) for n in _video_tag_names(lst, v)]
            existing = db.get_video_by_url(url)
            if existing:
                new_ids = [i for i in ids if i not in existing["tag_ids"]]
                if new_ids:
                    db.add_video_tags(existing["id"], new_ids)
                    report["tags_added"] += 1
                else:
                    report["unchanged"] += 1
            else:
                db.create_video(
                    source_url=url,
                    title=v.title or url,
                    tag_ids=ids,
                    import_id=imp["id"],
                )
                report["created"] += 1
        except Exception as exc:  # noqa: BLE001
            log.exception("Video-Import fehlgeschlagen: %s", v.url)
            report["errors"].append({"item": v.url, "message": str(exc)[:300]})

    log.info(
        "Import '%s': %d neu, %d Tags ergaenzt, %d unveraendert, %d Fehler",
        lst.name,
        report["created"],
        report["tags_added"],
        report["unchanged"],
        len(report["errors"]),
    )
    return report


def _fetch_tag_image(db: Database, settings: Settings, tag_id: int, url: str) -> None:
    if not url.startswith(("http://", "https://")):
        raise ImportError_("Tag-Bild muss eine http(s)-URL sein")
    try:
        with httpx.Client(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.content
    except httpx.HTTPError as exc:
        raise ImportError_(f"Tag-Bild konnte nicht geladen werden: {exc}") from exc
    if len(data) > 10 * 1024 * 1024:
        raise ImportError_("Tag-Bild ist zu gross (max. 10 MB)")
    try:
        tag_service.save_tag_image(db, settings, tag_id, data)
    except ValueError as exc:
        raise ImportError_(str(exc)) from exc


def import_progress(db: Database, imp: dict[str, Any]) -> dict[str, Any]:
    counts = db.count_by_status(import_id=imp["id"])
    total = sum(counts.values())
    return {
        "id": imp["id"],
        "name": imp["name"],
        "source": imp.get("source"),
        "created_at": imp["created_at"],
        "total": total,
        "queued": counts.get("queued", 0),
        "downloading": counts.get("downloading", 0),
        "downloaded": counts.get("downloaded", 0),
        "error": counts.get("error", 0),
    }
