# Vorkuratierte Videolisten

Listen im YAML-Format, die sich über die Admin-Oberfläche (Tab „Import“) in die Videobox
laden lassen – per Datei-Upload, durch Einfügen des Texts oder direkt von einer URL.

Diese Listen sind **nicht** Teil des Container-Images. Um eine Liste aus diesem Ordner zu
importieren, im Admin unter „Von URL“ die Raw-GitHub-Adresse eintragen, z. B.:

```
https://raw.githubusercontent.com/romulus-ai/vidibox/main/curated/zdf-einfach-erklaert.yaml
```

## Format

```yaml
name: ZDF Schule – Biologie              # Name des Imports (Pflicht, wird in der Übersicht angezeigt)
description: Kurze Beschreibung           # optional

# Optional: Tags vorab definieren, z. B. mit Bild.
# Tags, die nur bei Videos stehen, werden trotzdem automatisch angelegt.
tags:
  - name: Biologie
    image: https://example.org/bilder/biologie.jpg   # optional, http(s)-URL; wird nur gesetzt,
                                                     # wenn der Tag noch kein eigenes Bild hat

# Tags, die alle Videos dieser Liste bekommen (optional)
default_tags: [Biologie]

videos:
  # Langform
  - url: https://schule.zdf.de/video/gesunde-zaehne-einfach-erklaert-100
    tags: [Körper]                       # zusätzlich zu default_tags
    title: Warum gesunde Zähne wichtig sind   # optional, überschreibt den Titel der Mediathek

  # Kurzform: nur die URL, bekommt default_tags
  - https://schule.zdf.de/video/wolken-farbe-einfach-erklaert-100
```

Regeln:

- `videos[].url` ist das einzige Pflichtfeld. Alles, was yt-dlp kennt, funktioniert
  (ZDF, ZDF goes Schule, ARD, KiKA, ARTE, YouTube, …).
- Tag-Namen werden **ohne Beachtung von Groß-/Kleinschreibung** mit vorhandenen Tags abgeglichen;
  gibt es den Tag noch nicht, wird er angelegt.
- Ein Video bekommt `default_tags` **und** seine eigenen `tags`.
- Ist ein Video (gleiche URL) bereits in der Box, wird es **nicht erneut geladen** – es bekommt nur
  die fehlenden Tags ergänzt.
- Tags werden in der Kinder-UI alphabetisch sortiert (Zahlen zuerst).
- Tags ohne `image` zeigen in der Kinder-UI eine Collage aus den Thumbnails ihrer ersten vier Videos;
  ein Bild lässt sich hier per URL oder später im Admin per Upload setzen.
- Die Vorschau im Admin zeigt vor dem Import, was neu angelegt bzw. ergänzt würde.

## Eigene Listen

`vorlage.yaml` kopieren, Videos eintragen, fertig. Wer eine Liste teilen möchte: Pull Request in
diesen Ordner oder die Datei als Gist veröffentlichen und die Raw-URL weitergeben.
