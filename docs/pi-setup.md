# Raspberry Pi einrichten

Ziel: Der Videobox-Container läuft auf dem Pi, Chromium startet automatisch im Kiosk-Modus und zeigt
`http://localhost:8000/` auf dem Touchscreen. Ton kommt über HiFiBerry Amp + Lautsprecher.

Getestet gedacht für **Raspberry Pi OS (64-bit, Bookworm) mit Desktop** auf Pi 4 oder Pi 5.
Für Browser-Wiedergabe bis 720p ist der Pi 5 die entspanntere Wahl; auf dem Pi 4 sollte
`VIDEOBOX_MAX_RESOLUTION=720` (Default) nicht überschritten werden.

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

# HiFiBerry Amp2 / Amp4 / DAC+
dtoverlay=hifiberry-dacplus
# Amp+ (älteres Modell): dtoverlay=hifiberry-amp
# Amp100:                dtoverlay=hifiberry-amp100
# Pi 5: zusätzlich ggf.  dtoverlay=vc4-kms-v3d,noaudio
```

Zusätzlich HDMI-Audio unterdrücken, damit ALSA-Gerät 0 der HiFiBerry ist:

```ini
dtoverlay=vc4-kms-v3d,noaudio
```

Nach dem Neustart prüfen:

```bash
aplay -l                      # sollte "sndrpihifiberry" zeigen
speaker-test -c 2 -t wav -l 1 # Testton links/rechts
amixer scontrols              # Name des Mixers (z.B. 'Master' oder 'Digital')
```

Der Container steuert die Lautstärke über `amixer set <Mixer> …`. Standard ist `Master`; heißt der
Mixer anders (z.B. `Digital` beim DAC+/Amp2), beim Start `-e VIDEOBOX_AUDIO_CONTROL=Digital` setzen.

Falls Chromium den Ton über PipeWire ausgibt (Standard auf Bookworm), muss in den Desktop-
Lautstärke-Einstellungen (Rechtsklick auf das Lautsprechersymbol) die HiFiBerry als Ausgabegerät
gewählt werden.

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
  --device /dev/snd \
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
| Kein Ton                             | `aplay -l`, Ausgabegerät im Desktop, `--device /dev/snd` gesetzt? |
| Lautstärke-Buttons wirkungslos       | `docker exec videobox amixer scontrols` – Mixername prüfen       |
| Download bleibt auf „Fehler“         | `docker logs videobox`; ggf. Image aktualisieren (neues yt-dlp)  |
| Video ruckelt                        | `VIDEOBOX_MAX_RESOLUTION=540` setzen und neu laden               |
| Chromium zeigt Fehlerseite beim Start| `kiosk.sh` wartet auf `/api/health`; Docker-Dienst aktiv?        |
| „Permission denied“ auf /data        | Besitzer von `/var/lib/videobox` auf die Container-UID setzen    |
