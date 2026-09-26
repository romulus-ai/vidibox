"""Lautstaerkesteuerung ueber ALSA (amixer).

Auf Systemen ohne /dev/snd bzw. ohne amixer (z.B. Entwicklung auf dem Mac)
wird die Lautstaerke nur im Speicher gehalten.
"""

import logging
import re
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


class AudioControl:
    def __init__(self, max_volume: int, control: str = "Master"):
        self.max_volume = max_volume
        self.control = control
        self._memory_volume = min(50, max_volume)
        self.available = shutil.which("amixer") is not None and Path("/dev/snd").exists()
        if not self.available:
            log.info("amixer/ALSA nicht verfuegbar - Lautstaerke nur simuliert")

    def get(self) -> int:
        if not self.available:
            return self._memory_volume
        try:
            out = subprocess.run(
                ["amixer", "get", self.control], capture_output=True, text=True, timeout=3
            ).stdout
            m = re.search(r"\[(\d+)%\]", out)
            if m:
                return int(m.group(1))
        except Exception:  # noqa: BLE001
            log.warning("amixer get fehlgeschlagen", exc_info=True)
        return self._memory_volume

    def set(self, volume: int) -> int:
        volume = max(0, min(volume, self.max_volume))
        self._memory_volume = volume
        if self.available:
            try:
                subprocess.run(
                    ["amixer", "set", self.control, f"{volume}%"],
                    capture_output=True,
                    timeout=3,
                    check=False,
                )
            except Exception:  # noqa: BLE001
                log.warning("amixer set fehlgeschlagen", exc_info=True)
        return volume
