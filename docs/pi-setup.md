# Raspberry Pi einrichten

Ziel: Der Videobox-Container läuft auf dem Pi, Chromium startet automatisch im Kiosk-Modus und zeigt
`http://localhost:8000/` auf dem Touchscreen. Ton kommt über HiFiBerry Amp + Lautsprecher.

Getestet gedacht für **Raspberry Pi OS (64-bit, Bookworm) mit Desktop** auf Pi 4 oder Pi 5.

## 0. Anforderungen

Der Server-Container ist genügsam (~250 MB RAM, ffmpeg remuxt nur, kein Transcoding). Der
limitierende Faktor ist **Chromium, das auf demselben Gerät 720p-H.264 dekodiert**.

| | Minimum | Empfehlung |
|---|---|---|
| Board | Raspberry Pi 4 (2 GB) | **Raspberry Pi 5 (4 GB)** oder Pi 4 (4 GB) |
| OS | 64-bit (das Image gibt es nur für arm64/amd64) | Raspberry Pi OS 64-bit Bookworm mit Desktop |
| RAM | 2 GB | 4 GB |
| Speicher | 32 GB SD (~25 h Video) | 64 GB+ oder USB-SSD; ca. **1 GB pro Stunde Video** bei 720p |
| Netzteil | Pi 4: 5 V/3 A | Pi 5: offizielles 5 V/5 A (Verstärker-HAT hängt mit dran) |

- **Pi 4** nutzt den H.264-Hardwaredecoder in Chromium; **Pi 5** hat keinen, schafft 720p aber
  per Software problemlos. `VIDEOBOX_MAX_RESOLUTION=720` (Default) auf beiden nicht überschreiten.
- **Pi 3B+ (1 GB)** läuft nur mit `VIDEOBOX_MAX_RESOLUTION=480` und ruckelt bei der UI; Pi Zero 2 W
  (512 MB) ist für Chromium + Docker zu klein.
- **Orange Pi / andere SBCs** (arm64 + Docker) können den Container und Chromium ausführen, aber
  **HiFiBerry-HATs setzen den Raspberry-Pi-Header samt Device-Tree-Overlays voraus** – auf anderen
  Boards ist das nicht plug-and-play. Dort eher einen USB-DAC/-Verstärker verwenden.

## 1. Betriebssystem

1. Mit dem Raspberry Pi Imager „Raspberry Pi OS (64-bit)“ (mit Desktop) auf SD/SSD schreiben.
   In den Imager-Einstellungen: Benutzer `pi` (oder eigener Name), WLAN, SSH aktivieren.
2. Booten, dann per SSH verbinden und aktualisieren:

   ```bash
   sudo apt update && sudo apt full-upgrade -y
   sudo reboot
   ```

3. Auto-Login in den Desktop aktivieren:

   ```bash
   sudo raspi-config
   # System Options → Boot / Auto Login → Desktop Autologin
   ```

## 2. HiFiBerry Amp

In `/boot/firmware/config.txt` (ältere Images: `/boot/config.txt`):

```ini
# Onboard-Audio aus
dtparam=audio=off

# Genau EIN Overlay passend zum Board:
dtoverlay=hifiberry-dac        # MiniAmp, DAC (ohne "+")
# dtoverlay=hifiberry-dacplus  # Amp2 / Amp4 / DAC+
# dtoverlay=hifiberry-amp      # Amp+ (älteres Modell)
# dtoverlay=hifiberry-amp100   # Amp100
```

**MiniAmp:** 2×3 W an 4–8 Ω, wird direkt vom Pi versorgt (kein eigenes Netzteil, dafür ein
kräftiges für den Pi). Er hat **keinen Hardware-Lautstärkeregler** – `amixer` bietet dafür keinen
Mixer an; die Lautstärke wird in Software geregelt (PipeWire bzw. Browser, siehe unten).

Zusätzlich HDMI-Audio unterdrücken, damit ALSA-Gerät 0 der HiFiBerry ist:

```ini
dtoverlay=vc4-kms-v3d,noaudio
```

Nach dem Neustart prüfen:

```bash
aplay -l                      # sollte "sndrpihifiberry" zeigen
speaker-test -c 2 -t wav -l 1 # Testton links/rechts
wpctl status                  # PipeWire: HiFiBerry sollte Default-Sink sein
```

Ist die HiFiBerry nicht das Standard-Ausgabegerät: Rechtsklick auf das Lautsprechersymbol im
Desktop → HiFiBerry wählen, oder `wpctl set-default <ID aus wpctl status>`.

### Lautstärke

Die Lauter/Leiser-Buttons der Kinder-UI regeln die Lautstärke **im Browser** (gedeckelt durch
`VIDEOBOX_MAX_VOLUME`). Der Container braucht deshalb keinen Zugriff auf die Soundkarte. Die
Host-Lautstärke ist der physische Deckel und wird einmal fest eingestellt, z. B. im Kiosk-Skript
vor dem Chromium-Start:

```bash
wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.8     # PipeWire (Bookworm-Standard), funktioniert auch beim MiniAmp
# amixer -q set Digital 80%                   # nur Karten mit Hardware-Mixer (DAC+/Amp2), reines ALSA
```

## 3. Touchscreen

- **Offizielles 7"-Display (DSI):** wird automatisch erkannt. Bei Bedarf in `config.txt`
  `lcd_rotate=2` (Pi 4) bzw. `dtoverlay=vc4-kms-dsi-7inch` + Rotation über den Desktop
  (Pi 5).
- **HDMI-Touch-Displays:** Touch-Overlay/Anleitung des Herstellers befolgen.
- Bildschirmschoner/Blanking im Desktop aus: `sudo raspi-config` → Display Options → Screen Blanking → No.

## 4. Docker installieren

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker
docker --version
```

## 5. Videobox-Container starten

```bash
sudo mkdir -p /var/lib/videobox
sudo chown 999:999 /var/lib/videobox   # UID/GID des 'app'-Users im Image; alternativ: chmod 777

docker run -d --name videobox --restart unless-stopped \
  -p 8000:8000 \
  -v /var/lib/videobox:/data \
  -e VIDEOBOX_ADMIN_PIN=1234 \
  -e VIDEOBOX_MAX_VOLUME=70 \
  -e VIDEOBOX_MAX_RESOLUTION=720 \
  ghcr.io/romulus-ai/vidibox:latest
```

Prüfen: `curl http://localhost:8000/api/health` → `{"status":"ok",...}`.

Hinweis zur UID: Das Image läuft als unprivilegierter Benutzer. Falls beim Start
„Permission denied“ auf `/data` erscheint, die UID mit
`docker run --rm ghcr.io/romulus-ai/vidibox:latest id` auslesen und den `chown` entsprechend setzen.

### Update

```bash
docker pull ghcr.io/romulus-ai/vidibox:latest
docker rm -f videobox
docker run -d … (gleicher Befehl wie oben)
```

Optional automatisch mit [Watchtower](https://containrrr.dev/watchtower/):

```bash
docker run -d --name watchtower --restart unless-stopped \
  -v /var/run/docker.sock:/var/run/docker.sock \
  containrrr/watchtower --cleanup --interval 86400 videobox
```

## 6. Chromium im Kiosk-Modus

Raspberry Pi OS Bookworm nutzt standardmäßig Wayland (labwc bzw. wayfire). Der Autostart-Weg
unterscheidet sich je nach Compositor.

### Kiosk-Skript

`/home/pi/kiosk.sh` anlegen:

```bash
#!/bin/bash
# Warten, bis der Videobox-Container antwortet
until curl -fsS http://localhost:8000/api/health >/dev/null; do sleep 2; done

# Host-Lautstärke fest einstellen (physischer Deckel; Feinregelung macht die Kinder-UI im Browser)
wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.8 2>/dev/null || true

# Chromium-Absturzhinweis unterdrücken
sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' \
  ~/.config/chromium/Default/Preferences 2>/dev/null

exec chromium-browser \
  --kiosk \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-pinch \
  --overscroll-history-navigation=0 \
  --disable-translate \
  --no-first-run \
  --fast --fast-start \
  --autoplay-policy=no-user-gesture-required \
  --check-for-update-interval=31536000 \
  --touch-events=enabled \
  --enable-features=OverlayScrollbar \
  http://localhost:8000/
```

```bash
chmod +x /home/pi/kiosk.sh
```

Auf Bookworm heißt das Paket ggf. `chromium` statt `chromium-browser` (`which chromium chromium-browser`).

### Autostart – labwc (Pi OS ab Ende 2024, Standard auf Pi 5)

```bash
mkdir -p ~/.config/labwc
cat >> ~/.config/labwc/autostart <<'EOF'
/home/pi/kiosk.sh &
EOF
```

### Autostart – wayfire (ältere Bookworm-Images)

In `~/.config/wayfire.ini` ergänzen:

```ini
[autostart]
kiosk = /home/pi/kiosk.sh
```

### Autostart – X11/LXDE (Legacy oder „X11“ in raspi-config gewählt)

```bash
mkdir -p ~/.config/lxsession/LXDE-pi
cat > ~/.config/lxsession/LXDE-pi/autostart <<'EOF'
@xset s off
@xset -dpms
@xset s noblank
@/home/pi/kiosk.sh
EOF
```

Mauszeiger auf dem Touchscreen ausblenden (X11): `sudo apt install unclutter` und
`@unclutter -idle 0` in die Autostart-Datei; unter Wayland:
`--enable-features=OverlayScrollbar` reicht meist, ansonsten in labwc `<cursor>`-Settings nutzen.

### Kiosk-Ausbruch verhindern

- Tastatur nicht anschließen bzw. im Alltag entfernen; die Kinder-UI blockt `Esc`, `F11`, `Alt`/`Ctrl`.
- Die Admin-UI ist unter `http://<pi-ip>:8000/admin` vom Handy/Laptop erreichbar – auf dem Pi
  selbst muss man sie nicht öffnen.
- Optional: `--incognito` zusätzlich setzen, damit keine History entsteht.

## 7. Kontrolle

1. Neustart: `sudo reboot`. Nach dem Booten sollte der Container laufen und Chromium die
   Kinder-UI zeigen („Noch keine Videos da“).
2. Vom Handy `http://<pi-ip>:8000/admin` öffnen, PIN eingeben, Tag anlegen, Video-URL einfügen.
3. Nach dem Download erscheint das Video auf dem Touchscreen (die Tag-Ansicht aktualisiert sich
   alle 30 Sekunden bzw. bei „Zurück“).

## Fehlersuche

| Symptom                              | Prüfen                                                          |
|--------------------------------------|-----------------------------------------------------------------|
| Kein Ton                             | `aplay -l`, `wpctl status` (HiFiBerry Default-Sink?), Host-Lautstärke nicht auf 0, Overlay passt zum Board? |
| Zu leise trotz Lauter-Button         | `VIDEOBOX_MAX_VOLUME` erhöhen bzw. Host-Lautstärke im Kiosk-Skript anheben |
| Download bleibt auf „Fehler“         | `docker logs videobox`; ggf. Image aktualisieren (neues yt-dlp)  |
| Video ruckelt                        | `VIDEOBOX_MAX_RESOLUTION=540` setzen und neu laden               |
| Chromium zeigt Fehlerseite beim Start| `kiosk.sh` wartet auf `/api/health`; Docker-Dienst aktiv?        |
| „Permission denied“ auf /data        | Besitzer von `/var/lib/videobox` auf die Container-UID setzen    |
