/* Kinder-UI: Tags -> Videos -> Player. Kein Framework, keine Texteingabe. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const screens = { tags: $("screen-tags"), videos: $("screen-videos"), player: $("screen-player") };
  const player = $("player");
  const overlay = $("player-overlay");
  const VOLUME_STEP = 10;
  const OVERLAY_TIMEOUT = 4000;

  let volumeState = { volume: 50, max_volume: 100 };
  let overlayTimer = null;

  // ---------- Hilfen ----------

  function show(name) {
    Object.entries(screens).forEach(([k, el]) => el.classList.toggle("hidden", k !== name));
  }

  async function api(path, opts) {
    const res = await fetch(path, opts);
    if (!res.ok) throw new Error(`${path}: ${res.status}`);
    return res.json();
  }

  function fmtDuration(s) {
    if (!s) return "";
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}:${String(sec).padStart(2, "0")} min`;
  }

  function hueFor(text) {
    let h = 0;
    for (const ch of String(text || "")) h = (h * 31 + ch.charCodeAt(0)) % 360;
    return h;
  }

  function tile({ image, images, label, meta, cls, onClick }) {
    const btn = document.createElement("button");
    btn.className = `tile ${cls || ""}`;
    const img = document.createElement("div");
    img.className = "img";
    if (images && images.length > 0) {
      // Collage wie ein Ordner-Icon: 2x2 Zellen mit den ersten Video-Thumbnails,
      // fehlende Zellen bleiben leere Platzhalter
      img.classList.add("collage");
      for (let i = 0; i < 4; i++) {
        const cell = document.createElement("div");
        if (images[i]) cell.style.backgroundImage = `url("${images[i]}")`;
        img.appendChild(cell);
      }
    } else if (image) {
      img.style.backgroundImage = `url("${image}")`;
    } else {
      // Platzhalter: Anfangsbuchstabe auf einer aus dem Namen abgeleiteten Farbe,
      // damit Tags ohne Bild unterscheidbar bleiben
      img.classList.add("placeholder");
      img.style.backgroundColor = `hsl(${hueFor(label)}, 70%, 82%)`;
      img.textContent = (label || "?").slice(0, 1).toUpperCase();
    }
    btn.appendChild(img);
    const lbl = document.createElement("div");
    lbl.className = "label";
    lbl.textContent = label;
    btn.appendChild(lbl);
    if (meta) {
      const m = document.createElement("div");
      m.className = "meta";
      m.textContent = meta;
      btn.appendChild(m);
    }
    btn.addEventListener("click", onClick);
    return btn;
  }

  // ---------- Screen 1: Tags ----------

  async function loadTags() {
    const tags = await api("/api/tags");
    const grid = $("tag-grid");
    grid.replaceChildren();
    const visible = tags.filter((t) => t.video_count > 0);
    $("tags-empty").classList.toggle("hidden", visible.length > 0);
    for (const t of visible) {
      grid.appendChild(
        tile({
          image: t.image_url,
          images: t.has_own_image ? [] : t.thumbnail_urls,
          label: t.name,
          meta: `${t.video_count} Video${t.video_count === 1 ? "" : "s"}`,
          cls: "tag",
          onClick: () => openTag(t),
        })
      );
    }
    show("tags");
  }

  // ---------- Screen 2: Videos ----------

  async function openTag(tag) {
    const videos = await api(`/api/tags/${tag.id}/videos`);
    $("videos-title").textContent = tag.name;
    const grid = $("video-grid");
    grid.replaceChildren();
    for (const v of videos) {
      grid.appendChild(
        tile({
          image: v.thumbnail_url,
          label: v.title,
          meta: fmtDuration(v.duration_s),
          onClick: () => play(v),
        })
      );
    }
    grid.scrollTop = 0;
    show("videos");
  }

  // ---------- Screen 3: Player ----------

  function play(video) {
    $("player-title").textContent = video.title;
    player.src = video.media_url;
    show("player");
    showOverlay();
    player.play().catch(() => {});
  }

  function stop() {
    player.pause();
    player.removeAttribute("src");
    player.load();
    show("videos");
  }

  function updatePlayIcon() {
    $("icon-play").classList.toggle("hidden", !player.paused);
    $("icon-pause").classList.toggle("hidden", player.paused);
  }

  function showOverlay() {
    overlay.classList.remove("faded");
    clearTimeout(overlayTimer);
    overlayTimer = setTimeout(() => {
      if (!player.paused) overlay.classList.add("faded");
    }, OVERLAY_TIMEOUT);
  }

  player.addEventListener("play", updatePlayIcon);
  player.addEventListener("pause", () => { updatePlayIcon(); showOverlay(); });
  player.addEventListener("ended", stop);
  player.addEventListener("error", stop);
  player.addEventListener("timeupdate", () => {
    if (player.duration) {
      $("progress-fill").style.width = `${(player.currentTime / player.duration) * 100}%`;
    }
  });

  screens.player.addEventListener("click", (e) => {
    if (e.target.closest("button")) return;
    if (overlay.classList.contains("faded")) showOverlay();
    else togglePlay();
  });

  function togglePlay() {
    if (player.paused) player.play().catch(() => {});
    else player.pause();
    showOverlay();
  }

  // ---------- Lautstaerke ----------

  // Die Lautstaerke wird ausschliesslich im Browser geregelt (player.volume). Der Server
  // liefert nur den Deckel (max_volume); der gewaehlte Wert bleibt im localStorage erhalten.
  const VOLUME_KEY = "videobox.volume";
  let volumeHintTimer = null;

  async function loadVolume() {
    try {
      const s = await api("/api/settings");
      volumeState.max_volume = s.max_volume;
    } catch (_) {
      /* Deckel aus dem Default */
    }
    const saved = parseInt(localStorage.getItem(VOLUME_KEY), 10);
    volumeState.volume = Number.isFinite(saved) ? saved : Math.min(50, volumeState.max_volume);
    applyVolume();
  }

  function applyVolume() {
    volumeState.volume = Math.max(0, Math.min(volumeState.max_volume, volumeState.volume));
    player.volume = volumeState.volume / 100;
    player.muted = volumeState.volume === 0;
  }

  function changeVolume(delta) {
    volumeState.volume += delta;
    applyVolume();
    localStorage.setItem(VOLUME_KEY, String(volumeState.volume));
    showVolumeHint();
    showOverlay();
  }

  function showVolumeHint() {
    const hint = $("volume-hint");
    const steps = Math.round(volumeState.max_volume / VOLUME_STEP) || 1;
    const filled = Math.round((volumeState.volume / volumeState.max_volume) * steps);
    hint.textContent = "\u25CF".repeat(filled) + "\u25CB".repeat(Math.max(0, steps - filled));
    hint.classList.remove("hidden");
    clearTimeout(volumeHintTimer);
    volumeHintTimer = setTimeout(() => hint.classList.add("hidden"), 1500);
  }

  // ---------- Events ----------

  $("btn-back-tags").addEventListener("click", loadTags);
  $("btn-back-videos").addEventListener("click", stop);
  $("btn-play").addEventListener("click", togglePlay);
  $("btn-vol-down").addEventListener("click", () => changeVolume(-VOLUME_STEP));
  $("btn-vol-up").addEventListener("click", () => changeVolume(VOLUME_STEP));

  // Kiosk-Haertung: kein Kontextmenue, kein Drag, keine Tastenkuerzel zum Verlassen
  document.addEventListener("contextmenu", (e) => e.preventDefault());
  document.addEventListener("dragstart", (e) => e.preventDefault());
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" || e.key === "F11" || e.altKey || e.ctrlKey || e.metaKey) e.preventDefault();
  });

  // ---------- Auto-Reload nach Server-Update ----------
  // Nach einem Image-Update (Container-Neustart) wuerde der Kiosk-Browser sonst mit altem
  // CSS/JS weiterlaufen. Aendert sich die build_id, wird die Seite neu geladen - aber nur auf
  // der Uebersicht, nie waehrend ein Video laeuft.
  let buildId = null;

  async function checkBuild() {
    try {
      const h = await api("/api/health");
      if (buildId === null) buildId = h.build_id;
      else if (h.build_id !== buildId && !screens.tags.classList.contains("hidden")) {
        location.reload();
      }
    } catch (_) {
      /* Server gerade nicht erreichbar (z.B. Neustart) */
    }
  }

  // Start
  checkBuild();
  loadVolume();
  loadTags();
  // Tags regelmaessig aktualisieren, damit neue Downloads ohne Neustart erscheinen
  setInterval(() => {
    checkBuild();
    if (!screens.tags.classList.contains("hidden")) loadTags();
  }, 30000);
})();
