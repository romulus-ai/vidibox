from conftest import png_bytes, run_queue

URL = "https://www.zdf.de/kinder/beispiel-{n}.html"


def test_tag_crud(admin):
    r = admin.post("/api/admin/tags", json={"name": "Deutsch"})
    assert r.status_code == 201
    tag = r.json()
    assert tag["image_url"] is None and tag["video_count"] == 0

    # Doppelter Name (case-insensitiv) wird abgelehnt
    assert admin.post("/api/admin/tags", json={"name": "deutsch"}).status_code == 409

    r = admin.put(f"/api/admin/tags/{tag['id']}", json={"name": "Lesen", "sort_order": 5})
    assert r.json()["name"] == "Lesen" and r.json()["sort_order"] == 5

    assert admin.delete(f"/api/admin/tags/{tag['id']}").status_code == 204
    assert admin.get("/api/admin/tags").json() == []


def test_tag_sorting(admin):
    b = admin.post("/api/admin/tags", json={"name": "B", "sort_order": 2}).json()
    a = admin.post("/api/admin/tags", json={"name": "A", "sort_order": 1}).json()
    ids = [t["id"] for t in admin.get("/api/admin/tags").json()]
    assert ids == [a["id"], b["id"]]


def test_tag_image_upload_and_fallback(admin, worker, settings):
    tag = admin.post("/api/admin/tags", json={"name": "Sachkunde"}).json()

    # Zwei Videos, das zuerst angelegte liefert das Fallback-Bild
    v1 = admin.post("/api/admin/videos", json={"url": URL.format(n=1), "tag_ids": [tag["id"]]})
    v2 = admin.post("/api/admin/videos", json={"url": URL.format(n=2), "tag_ids": [tag["id"]]})
    run_queue(worker)
    v1_thumb = admin.get(f"/api/admin/videos/{v1.json()['id']}").json()["thumbnail_url"]
    assert v2.status_code == 201

    t = admin.get("/api/admin/tags").json()[0]
    assert t["has_own_image"] is False
    assert t["image_url"] == v1_thumb
    assert len(t["thumbnail_urls"]) == 2 and t["thumbnail_urls"][0] == v1_thumb
    assert t["video_count"] == 2

    # Eigenes Bild hochladen -> wird bevorzugt und quadratisch skaliert
    r = admin.post(
        f"/api/admin/tags/{tag['id']}/image",
        files={"file": ("bild.png", png_bytes(), "image/png")},
    )
    assert r.status_code == 200
    assert r.json()["has_own_image"] is True
    assert r.json()["image_url"] == f"/media/tags/{tag['id']}.jpg"
    assert r.json()["thumbnail_urls"] == []  # eigenes Bild, keine Collage

    from PIL import Image

    img = Image.open(settings.tags_dir / f"{tag['id']}.jpg")
    assert img.size == (512, 512)

    assert admin.get(r.json()["image_url"]).status_code == 200

    # Ungueltige Datei
    r = admin.post(
        f"/api/admin/tags/{tag['id']}/image",
        files={"file": ("x.png", b"kein bild", "image/png")},
    )
    assert r.status_code == 400

    # Bild entfernen -> zurueck zum Fallback
    r = admin.delete(f"/api/admin/tags/{tag['id']}/image")
    assert r.json()["has_own_image"] is False
    assert r.json()["image_url"] == v1_thumb
    assert not (settings.tags_dir / f"{tag['id']}.jpg").exists()


def test_deleting_tag_keeps_videos(admin, worker):
    tag = admin.post("/api/admin/tags", json={"name": "X"}).json()
    video = admin.post("/api/admin/videos", json={"url": URL.format(n=9), "tag_ids": [tag["id"]]})
    run_queue(worker)
    admin.delete(f"/api/admin/tags/{tag['id']}")
    v = admin.get(f"/api/admin/videos/{video.json()['id']}").json()
    assert v["status"] == "downloaded" and v["tag_ids"] == []
    assert admin.get(f"/api/tags/{tag['id']}/videos").status_code == 404


def test_tag_collage_limits_to_four_oldest(admin, worker):
    tag = admin.post("/api/admin/tags", json={"name": "Viele"}).json()
    created = [
        admin.post(
            "/api/admin/videos", json={"url": URL.format(n=i), "tag_ids": [tag["id"]]}
        ).json()["id"]
        for i in range(6)
    ]
    run_queue(worker)
    kids_tag = next(t for t in admin.get("/api/tags").json() if t["id"] == tag["id"])
    assert kids_tag["thumbnail_urls"] == [f"/media/thumbs/{i}.jpg" for i in created[:4]]
    assert kids_tag["image_url"] == kids_tag["thumbnail_urls"][0]
    all_tag = admin.get("/api/tags").json()[0]
    assert len(all_tag["thumbnail_urls"]) == 4
