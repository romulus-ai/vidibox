import io

import httpx
import pytest
from conftest import png_bytes, run_queue

from videobox.services import importer

LIST = """
name: Testliste
description: Beschreibung
tags:
  - name: Biologie
default_tags: [Biologie]
videos:
  - url: https://schule.zdf.de/video/a-100
    tags: [Körper]
    title: Eigener Titel
  - https://schule.zdf.de/video/b-100
  - url: https://www.zdf.de/video/a-100
    tags: [Nochmal]
"""


# ---------- Parser ----------


def test_parse_shorthand_and_defaults():
    lst = importer.parse_list(LIST)
    assert lst.name == "Testliste"
    assert [v.url for v in lst.videos][:2] == [
        "https://schule.zdf.de/video/a-100",
        "https://schule.zdf.de/video/b-100",
    ]
    assert lst.videos[0].tags == ["Körper"] and lst.videos[0].title == "Eigener Titel"
    assert lst.videos[1].tags == [] and lst.videos[1].title is None
    assert lst.default_tags == ["Biologie"]


def test_parse_tags_as_comma_string():
    lst = importer.parse_list("videos:\n  - url: https://x.org/v\n    tags: a, b\n")
    assert lst.videos[0].tags == ["a", "b"]


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("videos: [\n - url: 1", "YAML-Fehler (Zeile 2)"),
        ("- nur eine liste", "YAML-Objekt"),
        ("name: x", "videos"),
        ("videos: []", "videos"),
        ("videos:\n  - url: ftp://example.org/v", "http"),
        ("videos:\n  - title: nur titel", "url"),
    ],
)
def test_parse_errors(text, fragment):
    with pytest.raises(importer.ImportError_) as exc:
        importer.parse_list(text)
    assert fragment in str(exc.value)


# ---------- Preview / Import ----------


def test_preview_does_not_write(admin):
    r = admin.post("/api/admin/import/preview", json={"yaml": LIST})
    assert r.status_code == 200
    p = r.json()
    assert p["name"] == "Testliste"
    assert p["new_tags"] == ["Biologie", "Körper"]
    assert [i["action"] for i in p["items"]] == ["create", "create", "duplicate"]
    assert p["items"][0]["tags"] == ["Biologie", "Körper"]
    assert p["create"] == 2 and p["duplicate"] == 1
    assert admin.get("/api/admin/videos").json() == []
    assert admin.get("/api/admin/tags").json() == []


def test_import_creates_tags_and_queues_videos(admin, worker):
    r = admin.post("/api/admin/import", json={"yaml": LIST})
    assert r.status_code == 201
    rep = r.json()
    assert rep["created"] == 2 and rep["errors"] == []
    assert rep["tags_created"] == ["Biologie", "Körper"]

    tags = {t["name"]: t for t in admin.get("/api/admin/tags").json()}

    videos = {v["source_url"]: v for v in admin.get("/api/admin/videos").json()}
    a = videos["https://www.zdf.de/video/a-100"]
    b = videos["https://www.zdf.de/video/b-100"]
    assert a["status"] == "queued" and a["title"] == "Eigener Titel"
    assert set(a["tag_ids"]) == {tags["Biologie"]["id"], tags["Körper"]["id"]}
    assert b["tag_ids"] == [tags["Biologie"]["id"]]
    assert a["import_id"] == rep["import_id"]

    # Fortschritt
    prog = admin.get("/api/admin/imports").json()
    assert prog[0]["total"] == 2 and prog[0]["queued"] == 2 and prog[0]["downloaded"] == 0

    run_queue(worker)

    prog = admin.get("/api/admin/imports").json()[0]
    assert prog["downloaded"] == 2 and prog["queued"] == 0
    a = admin.get(f"/api/admin/videos/{a['id']}").json()
    assert a["title"] == "Eigener Titel"  # Listen-Titel hat Vorrang
    b = admin.get(f"/api/admin/videos/{b['id']}").json()
    assert b["title"].startswith("Titel fuer")

    vids = admin.get(f"/api/admin/imports/{rep['import_id']}/videos").json()
    assert [v["id"] for v in vids] == [a["id"], b["id"]]  # Listenreihenfolge


def test_import_reshuffles_collages(admin, app):
    before = app.state.db.collage_seed()
    admin.post("/api/admin/import", json={"yaml": LIST})
    assert app.state.db.collage_seed() != before


def test_reimport_only_adds_tags(admin, worker, backend):
    admin.post("/api/admin/import", json={"yaml": LIST})
    run_queue(worker)
    assert len(backend.calls) == 2

    again = LIST.replace("tags: [Körper]", "tags: [Körper, Klasse 3]")
    p = admin.post("/api/admin/import/preview", json={"yaml": again}).json()
    assert [i["action"] for i in p["items"]] == ["add_tags", "unchanged", "duplicate"]
    assert p["new_tags"] == ["Klasse 3"]

    rep = admin.post("/api/admin/import", json={"yaml": again}).json()
    assert rep["created"] == 0 and rep["tags_added"] == 1 and rep["unchanged"] == 1
    assert rep["tags_created"] == ["Klasse 3"]
    assert len(backend.calls) == 2  # kein erneuter Download
    videos = admin.get("/api/admin/videos").json()
    assert all(v["status"] == "downloaded" for v in videos)
    # Zweiter Import hat keine eigenen Videos
    imps = admin.get("/api/admin/imports").json()
    assert imps[0]["total"] == 0 and imps[1]["total"] == 2


def test_import_matches_existing_tags_case_insensitive(admin):
    admin.post("/api/admin/tags", json={"name": "biologie"})
    rep = admin.post("/api/admin/import", json={"yaml": LIST}).json()
    assert rep["tags_created"] == ["Körper"]
    assert [t["name"] for t in admin.get("/api/admin/tags").json()] == ["biologie", "Körper"]


def test_import_progress_counts_errors(admin, worker, backend):
    backend.fail_urls.add("https://www.zdf.de/video/b-100")
    rep = admin.post("/api/admin/import", json={"yaml": LIST}).json()
    run_queue(worker)
    prog = admin.get("/api/admin/imports").json()[0]
    assert prog["downloaded"] == 1 and prog["error"] == 1
    vids = admin.get(f"/api/admin/imports/{rep['import_id']}/videos").json()
    assert vids[1]["status"] == "error"


def test_delete_import_keeps_videos(admin):
    rep = admin.post("/api/admin/import", json={"yaml": LIST}).json()
    assert admin.delete(f"/api/admin/imports/{rep['import_id']}").status_code == 204
    assert admin.get("/api/admin/imports").json() == []
    videos = admin.get("/api/admin/videos").json()
    assert len(videos) == 2 and all(v["import_id"] is None for v in videos)


def test_import_file_upload(admin):
    r = admin.post(
        "/api/admin/import/file",
        files={"file": ("liste.yaml", LIST.encode(), "text/yaml")},
    )
    assert r.status_code == 201
    assert admin.get("/api/admin/imports").json()[0]["source"] == "liste.yaml"


def test_import_from_url_and_tag_image(admin, monkeypatch, settings):
    yaml_with_image = LIST.replace(
        "- name: Biologie", "- name: Biologie\n    image: https://img.example/bio.png"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "lists.example":
            return httpx.Response(200, content=yaml_with_image.encode())
        if request.url.host == "img.example":
            return httpx.Response(200, content=png_bytes())
        return httpx.Response(404)

    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(importer.httpx, "Client", fake_client)

    r = admin.post("/api/admin/import", json={"url": "https://lists.example/liste.yaml"})
    assert r.status_code == 201, r.text
    assert r.json()["errors"] == []
    assert admin.get("/api/admin/imports").json()[0]["source"] == "https://lists.example/liste.yaml"
    bio = next(t for t in admin.get("/api/admin/tags").json() if t["name"] == "Biologie")
    assert bio["has_own_image"] is True
    assert (settings.tags_dir / f"{bio['id']}.jpg").exists()

    # Unerreichbare Liste liefert eine verstaendliche Meldung
    r = admin.post("/api/admin/import", json={"url": "https://nope.example/x.yaml"})
    assert r.status_code == 400 and "geladen" in r.json()["detail"]


def test_import_requires_input(admin):
    assert admin.post("/api/admin/import", json={}).status_code == 400
    assert admin.post("/api/admin/import", json={"yaml": "   "}).status_code == 400
    assert admin.post("/api/admin/import/preview", json={"yaml": "videos: 1"}).status_code == 400


def test_curated_lists_are_valid():
    from pathlib import Path

    for f in Path(__file__).parent.parent.joinpath("curated").glob("*.yaml"):
        lst = importer.parse_list(f.read_text())
        assert lst.videos, f
        urls = [v.url for v in lst.videos]
        assert len(urls) == len(set(urls)), f"doppelte URL in {f}"


def test_oversized_list_rejected():
    big = "videos:\n" + "".join(f"  - https://x.org/{i}\n" for i in range(60000))
    with pytest.raises(importer.ImportError_):
        importer.parse_list(big)
    # Datei-Upload ebenfalls
    _ = io.BytesIO(big.encode())
