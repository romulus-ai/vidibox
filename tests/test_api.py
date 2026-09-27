from conftest import PIN, run_queue

URL = "https://www.zdf.de/kinder/beispiel-100.html"


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_admin_requires_login(client):
    assert client.get("/api/admin/videos").status_code == 401
    assert client.post("/api/admin/login", json={"pin": "0000"}).status_code == 401
    assert client.post("/api/admin/login", json={"pin": PIN}).status_code == 200
    assert client.get("/api/admin/videos").status_code == 200
    client.post("/api/admin/logout")
    assert client.get("/api/admin/videos").status_code == 401


def test_video_lifecycle(admin, worker):
    tag = admin.post("/api/admin/tags", json={"name": "Mathe"}).json()

    r = admin.post("/api/admin/videos", json={"url": URL, "tag_ids": [tag["id"]]})
    assert r.status_code == 201
    video = r.json()
    assert video["status"] == "queued"
    assert video["tag_ids"] == [tag["id"]]
    assert video["media_url"] is None

    # Duplikat wird abgelehnt
    assert admin.post("/api/admin/videos", json={"url": URL}).status_code == 409
    # Unbekannter Tag wird abgelehnt
    assert (
        admin.post("/api/admin/videos", json={"url": URL + "x", "tag_ids": [999]}).status_code
        == 400
    )

    # Kinder-UI sieht das Video noch nicht
    assert admin.get(f"/api/tags/{tag['id']}/videos").json() == []

    run_queue(worker)

    v = admin.get(f"/api/admin/videos/{video['id']}").json()
    assert v["status"] == "downloaded"
    assert v["title"].startswith("Titel fuer")
    assert v["duration_s"] == 125
    assert v["media_url"].endswith(".mp4")
    assert v["thumbnail_url"].endswith(".jpg")

    # Kinder-UI sieht das Video und der Tag hat ein Bild + Zaehler
    kids_videos = admin.get(f"/api/tags/{tag['id']}/videos").json()
    assert [k["id"] for k in kids_videos] == [video["id"]]
    kids_tags = admin.get("/api/tags").json()
    assert kids_tags[0]["id"] == 0 and kids_tags[0]["name"] == "Alle"
    assert kids_tags[0]["video_count"] == 1
    assert kids_tags[1]["video_count"] == 1
    assert kids_tags[1]["image_url"] == v["thumbnail_url"]

    # Media mit Range abrufbar
    m = admin.get(v["media_url"], headers={"Range": "bytes=0-9"})
    assert m.status_code == 206
    assert len(m.content) == 10

    # Bearbeiten
    r = admin.put(f"/api/admin/videos/{video['id']}", json={"title": "Neu", "tag_ids": []})
    assert r.json()["title"] == "Neu" and r.json()["tag_ids"] == []
    assert admin.get(f"/api/tags/{tag['id']}/videos").json() == []
    assert len(admin.get("/api/tags/0/videos").json()) == 1

    # Loeschen entfernt Dateien
    assert admin.delete(f"/api/admin/videos/{video['id']}").status_code == 204
    assert admin.get(f"/api/admin/videos/{video['id']}").status_code == 404
    assert admin.get(v["media_url"]).status_code == 404


def test_error_and_retry(admin, worker, backend):
    backend.fail_urls.add(URL)
    video = admin.post("/api/admin/videos", json={"url": URL}).json()
    run_queue(worker)

    v = admin.get(f"/api/admin/videos/{video['id']}").json()
    assert v["status"] == "error"
    assert "nicht verfuegbar" in v["error_msg"]
    # Kinder-UI zeigt Fehler-Videos nicht
    assert admin.get("/api/tags/0/videos").json() == []

    backend.fail_urls.clear()
    r = admin.post(f"/api/admin/videos/{video['id']}/retry")
    assert r.json()["status"] == "queued"
    run_queue(worker)
    assert admin.get(f"/api/admin/videos/{video['id']}").json()["status"] == "downloaded"


def test_manual_url_is_normalized(admin):
    r = admin.post("/api/admin/videos", json={"url": "https://schule.zdf.de/video/x-100?a=1#t"})
    assert r.json()["source_url"] == "https://www.zdf.de/video/x-100"
    # Dieselbe Quelle ueber die andere Domain ist ein Duplikat
    assert (
        admin.post("/api/admin/videos", json={"url": "https://www.zdf.de/video/x-100"}).status_code
        == 409
    )


def test_retry_resets_attempts(admin, worker, backend):
    backend.fail_urls.add(URL)
    video = admin.post("/api/admin/videos", json={"url": URL}).json()
    run_queue(worker)
    assert admin.get(f"/api/admin/videos/{video['id']}").json()["attempts"] == 1
    r = admin.post(f"/api/admin/videos/{video['id']}/retry")
    assert r.json()["attempts"] == 0 and r.json()["status"] == "queued"


def test_status_endpoint(admin):
    admin.post("/api/admin/videos", json={"url": URL})
    s = admin.get("/api/admin/status").json()
    assert s["queue_length"] == 1
    assert s["disk_free_bytes"] > 0
    assert s["video_counts"] == {"queued": 1}


def test_volume_is_capped(client):
    r = client.put("/api/volume", json={"volume": 100})
    assert r.json()["volume"] == 60
    assert r.json()["max_volume"] == 60
    assert client.get("/api/volume").json()["volume"] == 60
    assert client.put("/api/volume", json={"volume": 150}).status_code == 422
