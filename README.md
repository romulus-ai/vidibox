# Videobox

Kindgerechte Videobox für Lerninhalte aus Mediatheken (ZDF/ZDF goes Schule, ARD, KiKA, ARTE, YouTube, …).
Läuft als Container auf einem Raspberry Pi 4/5 mit Touchscreen; die Kinder-Oberfläche wird in
Chromium im Kiosk-Modus angezeigt.

- Inhalte werden **vorab per yt-dlp heruntergeladen** (kein Streaming, kein Buffering, unabhängig von
  Depublikation) und lokal als MP4/H.264 abgelegt.
- **Kinder-UI** (`/`): große Kacheln – Tags → Videos → Player. Keine Texteingabe, kein Verlassen.
- **Admin-UI** (`/admin`, PIN-geschützt): URL einfügen → Download, Tags anlegen/zuweisen, Tag-Bilder.
- **Listen-Import**: vorkuratierte YAML-Listen (Datei, Text oder URL) legen Tags an und stellen alle
  Videos in die Warteschlange; Fortschritt pro Import sichtbar. Beispiele und Format in [`curated/`](curated/).
- Tags ohne eigenes Bild verwenden automatisch das Thumbnail ihres ersten Videos.
- Die Download-Warteschlange liegt in SQLite und überlebt Neustarts: unterbrochene Downloads werden
  beim Start automatisch fortgesetzt.

## Stack

Python 3.12 · FastAPI · SQLite · yt-dlp + ffmpeg · Vanilla HTML/CSS/JS · Docker (multi-arch, GHCR)

## Betrieb auf dem Raspberry Pi

Vollständige Einrichtung (Raspberry Pi OS, Docker, HiFiBerry, Chromium-Kiosk) siehe
[`docs/pi-setup.md`](docs/pi-setup.md). Kurzfassung:

```bash
sudo mkdir -p /var/lib/videobox
docker run -d --name videobox --restart unless-stopped \
  -p 8000:8000 \
  -v /var/lib/videobox:/data \
  --device /dev/snd \
  -e VIDEOBOX_ADMIN_PIN=1234 \
  -e VIDEOBOX_MAX_VOLUME=70 \
  ghcr.io/romulus-ai/vidibox:latest
```

Kinder-UI: `http://<pi>:8000/` · Admin: `http://<pi>:8000/admin` (auch vom Handy/Laptop im LAN).

Update: `docker pull ghcr.io/romulus-ai/vidibox:latest && docker rm -f videobox && docker run …` (Daten
bleiben im Volume). Das Image wird wöchentlich neu gebaut, damit yt-dlp aktuell bleibt.

### Konfiguration (Umgebungsvariablen)

| Variable                   | Default  | Bedeutung                                   |
|----------------------------|----------|---------------------------------------------|
| `VIDEOBOX_ADMIN_PIN`       | `1234`   | PIN für die Admin-Oberfläche                |
| `VIDEOBOX_MAX_VOLUME`      | `70`     | Lautstärke-Deckel in Prozent                |
| `VIDEOBOX_AUDIO_CONTROL`   | `Master` | ALSA-Mixername (`amixer scontrols`)         |
| `VIDEOBOX_MAX_RESOLUTION`  | `720`    | Max. Videohöhe beim Download                |
| `VIDEOBOX_MAX_ATTEMPTS`    | `3`      | Wie oft ein durch Neustart unterbrochener Download erneut versucht wird |
| `VIDEOBOX_DATA_DIR`        | `/data`  | Datenbank, Videos, Thumbnails, Tag-Bilder   |
| `VIDEOBOX_PORT`            | `8000`   | HTTP-Port                                   |
| `VIDEOBOX_SESSION_HOURS`   | `12`     | Gültigkeit des Admin-Logins                 |

## Entwicklung (Mac/Linux)

Voraussetzungen: [uv](https://docs.astral.sh/uv/), `ffmpeg` (`brew install ffmpeg`).

```bash
uv sync
cp .env.example .env            # optional anpassen
uv run python -m videobox.main  # http://localhost:8000
uv run pytest
uv run ruff check . && uv run ruff format .
docker build -t videobox:dev .  # lokaler Container-Build
```

Ohne ALSA (`/dev/snd`) wird die Lautstärke nur simuliert; der Browser-Player übernimmt den Wert.

## API-Überblick

- Kinder (öffentlich): `GET /api/tags`, `GET /api/tags/{id}/videos` (`0` = Alle), `GET /api/videos/{id}`,
  `GET/PUT /api/volume`, `GET /media/videos/{file}` (mit HTTP-Range), `/media/thumbs/…`, `/media/tags/…`
- Admin (Cookie nach `POST /api/admin/login`): `GET/POST/PUT/DELETE /api/admin/videos[/{id}]`,
  `POST /api/admin/videos/{id}/retry`, `GET/POST/PUT/DELETE /api/admin/tags[/{id}]`,
  `POST/DELETE /api/admin/tags/{id}/image`, `GET /api/admin/status`,
  Import: `POST /api/admin/import[/preview]` (JSON `{yaml}` oder `{url}`), `POST /api/admin/import[/preview]/file`
  (Multipart), `GET /api/admin/imports`, `GET /api/admin/imports/{id}/videos`, `DELETE /api/admin/imports/{id}`
- System: `GET /api/health`, OpenAPI unter `/docs`

Video-Status: `queued → downloading → downloaded | error`.

## Hinweise

- `schule.zdf.de/video/<slug>` wird automatisch auf `www.zdf.de/video/<slug>` umgeschrieben (yt-dlp kennt
  die Schul-Domain nicht, die Videos sind identisch).
- Die rechtliche Bewertung von Downloads (Privatkopie) für eure Nutzung liegt bei euch.
