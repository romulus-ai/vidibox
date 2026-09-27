from conftest import png_bytes, run_queue

URL = "https://www.zdf.de/kinder/beispiel-{n}.html"


def test_tag_crud(admin):
    r = admin.post("/api/admin/tags", json={"name": "Deutsch"})
    assert r.status_code == 201
    tag = r.json()
    assert tag["image_url"] is None and tag["video_count"] == 0

    # Doppelter Name (case-insensitiv) wird abgelehnt
    assert admin.post("/api/admin/tags", json={"name": "deutsch"}).status_code == 409

    r = admin.put(f"/api/admin/tags/{tag['id']}", json={"name": "Lesen"})
    assert r.json()["name"] == "Lesen"

    assert admin.delete(f"/api/admin/tags/{tag['id']}").status_code == 204
    assert admin.get("/api/admin/tags").json() == []


def test_tags_sorted_alphabetically_numbers_first(admin):
    for name in ["Zebra", "apfel", "10 Dinge", "Ärger", "2 Dinge", "Alter 10-12", "Alter 5-7"]:
        admin.post("/api/admin/tags", json={"name": name})
    expected = ["2 Dinge", "10 Dinge", "Alter 5-7", "Alter 10-12", "apfel", "Ärger", "Zebra"]
    assert [t["name"] for t in admin.get("/api/admin/tags").json()] == expected
    kids = [t["name"] for t in admin.get("/api/tags").json()]
    assert kids[0] == "Alle" and kids[1:] == expected


def test_tag_image_upload(admin, worker, settings):
    tag = admin.post("/api/admin/tags", json={"name": "Sachkunde"}).json()

    admin.post("/api/admin/videos", json={"url": URL.format(n=1), "tag_ids": [tag["id"]]})
    admin.post("/api/admin/videos", json={"url": URL.format(n=2), "tag_ids": [tag["id"]]})
    run_queue(worker)

    # Ohne eigenes Bild: Collage aus den vorhandenen Thumbnails (hier zwei)
    t = admin.get("/api/admin/tags").json()[0]
    assert t["has_own_image"] is False
    assert t["image_url"] is None and len(t["thumbnail_urls"]) == 2
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

    # Bild entfernen -> wieder Collage
    r = admin.delete(f"/api/admin/tags/{tag['id']}/image")
    assert r.json()["has_own_image"] is False
    assert r.json()["image_url"] is None and len(r.json()["thumbnail_urls"]) == 2
    assert not (settings.tags_dir / f"{tag['id']}.jpg").exists()


def test_deleting_tag_keeps_videos(admin, worker):
    tag = admin.post("/api/admin/tags", json={"name": "X"}).json()
    video = admin.post("/api/admin/videos", json={"url": URL.format(n=9), "tag_ids": [tag["id"]]})
    run_queue(worker)
    admin.delete(f"/api/admin/tags/{tag['id']}")
    v = admin.get(f"/api/admin/videos/{video.json()['id']}").json()
    assert v["status"] == "downloaded" and v["tag_ids"] == []
    assert admin.get(f"/api/tags/{tag['id']}/videos").status_code == 404


def test_collage_is_random_but_stable_until_reshuffle(admin, worker, app):
    tag = admin.post("/api/admin/tags", json={"name": "Viele"}).json()
    created = [
        admin.post(
            "/api/admin/videos", json={"url": URL.format(n=i), "tag_ids": [tag["id"]]}
        ).json()["id"]
        for i in range(12)
    ]
    run_queue(worker)

    def collage():
        t = next(t for t in admin.get("/api/tags").json() if t["id"] == tag["id"])
        return t["thumbnail_urls"]

    first = collage()
    assert len(first) == 4 and len(set(first)) == 4
    assert all(u.startswith("/media/thumbs/") for u in first)
    assert set(u.split("/")[-1][:-4] for u in first) <= set(created)
    # Stabil zwischen Abfragen (kein Flackern beim Refresh)
    assert collage() == first
    # Nach dem Neuwuerfeln (wie nach einem Import) eine andere Auswahl - bei 12 Videos ist die
    # Wahrscheinlichkeit derselben vier Bilder in gleicher Reihenfolge vernachlaessigbar
    for _ in range(3):
        app.state.db.reshuffle_collages()
        if collage() != first:
            break
    else:
        raise AssertionError("Collage aendert sich nach reshuffle nicht")
    # "Alle" nutzt dieselbe Logik
    assert len(admin.get("/api/tags").json()[0]["thumbnail_urls"]) == 4
