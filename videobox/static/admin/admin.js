/* Admin-UI: Login, Videos (Download-Queue), Tags. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const POLL_MS = 3000;
  const STATUS_LABEL = {
    queued: "Wartend",
    downloading: "Lädt…",
    downloaded: "Fertig",
    error: "Fehler",
  };

  let tags = [];
  let videos = [];
  let imports = [];
  const expandedImports = new Set();
  let pollTimer = null;
  let editingVideoId = null;

  // ---------- API ----------

  async function api(path, opts = {}) {
    const res = await fetch(path, opts);
    if (res.status === 401) {
      showLogin();
      throw new Error("Nicht angemeldet");
    }
    if (!res.ok) {
      let msg = `${res.status}`;
      try {
        const body = await res.json();
        msg = body.detail || msg;
      } catch (_) { /* leer */ }
      throw new Error(msg);
    }
    if (res.status === 204) return null;
    return res.json();
  }

  const json = (method, body) => ({
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  // ---------- Login ----------

  function showLogin() {
    clearInterval(pollTimer);
    $("view-app").classList.add("hidden");
    $("view-login").classList.remove("hidden");
    $("login-pin").focus();
  }

  async function showApp() {
    $("view-login").classList.add("hidden");
    $("view-app").classList.remove("hidden");
    await refreshAll();
    clearInterval(pollTimer);
    pollTimer = setInterval(refreshAll, POLL_MS);
  }

  $("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("login-error").classList.add("hidden");
    try {
      await api("/api/admin/login", json("POST", { pin: $("login-pin").value }));
      $("login-pin").value = "";
      showApp();
    } catch (_) {
      $("login-error").classList.remove("hidden");
    }
  });

  $("btn-logout").addEventListener("click", async () => {
    await fetch("/api/admin/logout", { method: "POST" });
    showLogin();
  });

  // ---------- Tabs ----------

  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === btn));
      document.querySelectorAll(".tab-panel").forEach((p) => {
        p.classList.toggle("hidden", p.id !== `tab-${btn.dataset.tab}`);
      });
    });
  });

  // ---------- Daten laden ----------

  async function refreshAll() {
    try {
      const [status, t, v, imps] = await Promise.all([
        api("/api/admin/status"),
        api("/api/admin/tags"),
        api("/api/admin/videos"),
        api("/api/admin/imports"),
      ]);
      tags = t;
      videos = v;
      imports = imps;
      renderStatus(status);
      renderTagPicker($("add-tags"), []);
      renderVideos();
      renderTags();
      renderImports();
    } catch (_) { /* Login-Fall wird in api() behandelt */ }
  }

  function fmtBytes(b) {
    if (b == null) return "";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
    return `${b.toFixed(i >= 2 ? 1 : 0)} ${units[i]}`;
  }

  function fmtDuration(s) {
    if (!s) return "";
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")} min`;
  }

  function renderStatus(s) {
    $("status").textContent =
      `Warteschlange: ${s.queue_length} · Frei: ${fmtBytes(s.disk_free_bytes)} / ${fmtBytes(s.disk_total_bytes)} · yt-dlp ${s.ytdlp_version}`;
  }

  // ---------- Tag-Picker ----------

  function renderTagPicker(container, selectedIds) {
    // Auswahl beibehalten, falls der Nutzer gerade tippt
    const current = new Set(
      [...container.querySelectorAll("input:checked")].map((i) => Number(i.value))
    );
    const selected = current.size ? current : new Set(selectedIds);
    container.replaceChildren();
    if (!tags.length) {
      const p = document.createElement("span");
      p.className = "none";
      p.textContent = "Noch keine Tags – zuerst unter „Tags“ anlegen.";
      container.appendChild(p);
      return;
    }
    for (const t of tags) {
      const label = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = t.id;
      cb.checked = selected.has(t.id);
      label.append(cb, document.createTextNode(t.name));
      container.appendChild(label);
    }
  }

  const pickedIds = (container) =>
    [...container.querySelectorAll("input:checked")].map((i) => Number(i.value));

  // ---------- Videos ----------

  $("add-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("add-error");
    err.classList.add("hidden");
    try {
      await api("/api/admin/videos", json("POST", {
        url: $("add-url").value.trim(),
        tag_ids: pickedIds($("add-tags")),
      }));
      $("add-url").value = "";
      $("add-tags").querySelectorAll("input").forEach((i) => (i.checked = false));
      refreshAll();
    } catch (ex) {
      err.textContent = ex.message;
      err.classList.remove("hidden");
    }
  });

  $("filter-status").addEventListener("change", renderVideos);

  function renderVideos() {
    const filter = $("filter-status").value;
    const list = $("video-list");
    list.replaceChildren();
    const tagName = (id) => tags.find((t) => t.id === id)?.name || `#${id}`;
    const shown = videos.filter((v) => !filter || v.status === filter);
    if (!shown.length) {
      list.innerHTML = '<p class="hint">Keine Videos.</p>';
      return;
    }
    for (const v of shown) {
      const item = document.createElement("div");
      item.className = "item";

      const thumb = document.createElement("div");
      thumb.className = "thumb";
      if (v.thumbnail_url) thumb.style.backgroundImage = `url("${v.thumbnail_url}")`;

      const info = document.createElement("div");
      const title = document.createElement("div");
      title.className = "title";
      title.textContent = v.title;
      const sub = document.createElement("div");
      sub.className = "sub";
      const badge = document.createElement("span");
      badge.className = `badge ${v.status}`;
      badge.textContent = STATUS_LABEL[v.status] || v.status;
      sub.appendChild(badge);
      const imp = v.import_id ? imports.find((i) => i.id === v.import_id) : null;
      const parts = [
        v.provider, fmtDuration(v.duration_s), fmtBytes(v.file_size),
        imp ? `Import: ${imp.name}` : null,
      ].filter(Boolean);
      sub.appendChild(document.createTextNode(parts.join(" · ")));
      if (v.status === "error" && v.error_msg) {
        const em = document.createElement("div");
        em.className = "error";
        em.textContent = v.error_msg;
        sub.appendChild(em);
      }
      const url = document.createElement("div");
      url.className = "sub";
      url.textContent = v.source_url;
      const tagRow = document.createElement("div");
      tagRow.className = "tags";
      for (const id of v.tag_ids) {
        const c = document.createElement("span");
        c.className = "chip";
        c.textContent = tagName(id);
        tagRow.appendChild(c);
      }
      info.append(title, sub, url, tagRow);

      const actions = document.createElement("div");
      actions.className = "actions";
      actions.appendChild(button("Bearbeiten", "btn small", () => openEdit(v)));
      if (v.status === "error") {
        actions.appendChild(button("Erneut", "btn small", async () => {
          await api(`/api/admin/videos/${v.id}/retry`, { method: "POST" });
          refreshAll();
        }));
      }
      const del = button("Löschen", "btn small danger", async () => {
        if (!confirm(`„${v.title}“ wirklich löschen?`)) return;
        await api(`/api/admin/videos/${v.id}`, { method: "DELETE" });
        refreshAll();
      });
      del.disabled = v.status === "downloading";
      actions.appendChild(del);

      item.append(thumb, info, actions);
      list.appendChild(item);
    }
  }

  function button(text, cls, onClick) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = cls;
    b.textContent = text;
    b.addEventListener("click", onClick);
    return b;
  }

  // ---------- Video bearbeiten ----------

  function openEdit(v) {
    editingVideoId = v.id;
    $("edit-title").value = v.title;
    $("edit-url").textContent = v.source_url;
    $("edit-tags").replaceChildren();
    renderTagPicker($("edit-tags"), v.tag_ids);
    $("edit-dialog").showModal();
  }

  $("edit-cancel").addEventListener("click", () => $("edit-dialog").close());
  $("edit-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api(`/api/admin/videos/${editingVideoId}`, json("PUT", {
        title: $("edit-title").value.trim(),
        tag_ids: pickedIds($("edit-tags")),
      }));
      $("edit-dialog").close();
      refreshAll();
    } catch (ex) {
      alert(ex.message);
    }
  });

  // ---------- Tags ----------

  $("tag-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("tag-error");
    err.classList.add("hidden");
    try {
      await api("/api/admin/tags", json("POST", { name: $("tag-name").value.trim() }));
      $("tag-name").value = "";
      refreshAll();
    } catch (ex) {
      err.textContent = ex.message;
      err.classList.remove("hidden");
    }
  });

  function renderTags() {
    const list = $("tag-list");
    list.replaceChildren();
    if (!tags.length) {
      list.innerHTML = '<p class="hint">Noch keine Tags.</p>';
      return;
    }
    tags.forEach((t, idx) => {
      const item = document.createElement("div");
      item.className = "item tag";

      const thumb = document.createElement("div");
      thumb.className = "thumb";
      if (t.image_url) thumb.style.backgroundImage = `url("${t.image_url}?v=${Date.now()}")`;

      const info = document.createElement("div");
      const title = document.createElement("div");
      title.className = "title";
      title.textContent = t.name;
      const sub = document.createElement("div");
      sub.className = "sub";
      sub.textContent =
        `${t.video_count} Video(s) · ${t.has_own_image ? "eigenes Bild" : "Collage aus Video-Thumbnails"}`;
      info.append(title, sub);

      const actions = document.createElement("div");
      actions.className = "actions";

      const up = button("▲", "btn small", () => move(idx, -1));
      up.disabled = idx === 0;
      const down = button("▼", "btn small", () => move(idx, 1));
      down.disabled = idx === tags.length - 1;
      actions.append(up, down);

      actions.appendChild(button("Umbenennen", "btn small", async () => {
        const name = prompt("Neuer Name:", t.name);
        if (!name || name.trim() === t.name) return;
        try {
          await api(`/api/admin/tags/${t.id}`, json("PUT", { name: name.trim() }));
          refreshAll();
        } catch (ex) { alert(ex.message); }
      }));

      const fileInput = document.createElement("input");
      fileInput.type = "file";
      fileInput.accept = "image/*";
      fileInput.className = "hidden";
      fileInput.addEventListener("change", async () => {
        if (!fileInput.files[0]) return;
        const fd = new FormData();
        fd.append("file", fileInput.files[0]);
        try {
          await api(`/api/admin/tags/${t.id}/image`, { method: "POST", body: fd });
          refreshAll();
        } catch (ex) { alert(ex.message); }
      });
      actions.appendChild(fileInput);
      actions.appendChild(button("Bild wählen", "btn small", () => fileInput.click()));

      if (t.has_own_image) {
        actions.appendChild(button("Bild entfernen", "btn small", async () => {
          await api(`/api/admin/tags/${t.id}/image`, { method: "DELETE" });
          refreshAll();
        }));
      }

      actions.appendChild(button("Löschen", "btn small danger", async () => {
        if (!confirm(`Tag „${t.name}“ löschen? Videos bleiben erhalten.`)) return;
        await api(`/api/admin/tags/${t.id}`, { method: "DELETE" });
        refreshAll();
      }));

      item.append(thumb, info, actions);
      list.appendChild(item);
    });
  }

  async function move(idx, dir) {
    const other = idx + dir;
    if (other < 0 || other >= tags.length) return;
    // Reihenfolge komplett neu durchnummerieren, damit sort_order eindeutig ist
    const order = tags.map((t) => t.id);
    [order[idx], order[other]] = [order[other], order[idx]];
    await Promise.all(
      order.map((id, i) => api(`/api/admin/tags/${id}`, json("PUT", { sort_order: i })))
    );
    refreshAll();
  }

  // ---------- Import ----------

  const ACTION_LABEL = {
    create: "Neu",
    add_tags: "Tags ergänzen",
    unchanged: "Vorhanden",
    duplicate: "Doppelt in Liste",
  };

  document.querySelectorAll('input[name="import-mode"]').forEach((r) => {
    r.addEventListener("change", () => {
      document.querySelectorAll(".import-mode").forEach((el) => {
        el.classList.toggle("hidden", el.id !== `import-mode-${r.value}`);
      });
      hideImportResults();
    });
  });

  function hideImportResults() {
    $("import-error").classList.add("hidden");
    $("import-preview").classList.add("hidden");
    $("import-report").classList.add("hidden");
  }

  /** Sendet die Liste an /api/admin/import[/preview] – je nach Modus JSON oder Multipart. */
  async function submitImport(preview) {
    const mode = document.querySelector('input[name="import-mode"]:checked').value;
    const base = preview ? "/api/admin/import/preview" : "/api/admin/import";
    if (mode === "file") {
      const f = $("import-file").files[0];
      if (!f) throw new Error("Bitte eine YAML-Datei auswählen");
      const fd = new FormData();
      fd.append("file", f);
      return api(`${base}/file`, { method: "POST", body: fd });
    }
    const body = mode === "url"
      ? { url: $("import-url").value.trim() }
      : { yaml: $("import-yaml").value };
    if (mode === "url" && !body.url) throw new Error("Bitte eine URL angeben");
    if (mode === "yaml" && !body.yaml.trim()) throw new Error("Bitte YAML einfügen");
    return api(base, json("POST", body));
  }

  async function withButtons(fn) {
    const btns = [$("btn-import-preview"), $("btn-import-run")];
    btns.forEach((b) => (b.disabled = true));
    hideImportResults();
    try {
      await fn();
    } catch (ex) {
      $("import-error").textContent = ex.message;
      $("import-error").classList.remove("hidden");
    } finally {
      btns.forEach((b) => (b.disabled = false));
    }
  }

  $("btn-import-preview").addEventListener("click", () =>
    withButtons(async () => renderPreview(await submitImport(true)))
  );

  $("btn-import-run").addEventListener("click", () =>
    withButtons(async () => {
      const report = await submitImport(false);
      renderReport(report);
      expandedImports.add(report.import_id);
      refreshAll();
    })
  );

  function renderPreview(p) {
    const box = $("import-preview");
    box.replaceChildren();
    const sum = document.createElement("div");
    sum.className = "summary";
    sum.innerHTML = `<strong>${esc(p.name)}</strong>` +
      (p.description ? ` – ${esc(p.description)}` : "") +
      `<br>${p.create} neu · ${p.add_tags} Tags ergänzen · ${p.unchanged} vorhanden` +
      (p.duplicate ? ` · ${p.duplicate} doppelt` : "") +
      (p.new_tags.length ? `<br>Neue Tags: ${p.new_tags.map(esc).join(", ")}` : "");
    box.appendChild(sum);

    const table = document.createElement("table");
    table.className = "preview-table";
    table.innerHTML = "<thead><tr><th>Aktion</th><th>Video</th><th>Tags</th></tr></thead>";
    const tbody = document.createElement("tbody");
    for (const it of p.items) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td><span class="action ${it.action}">${ACTION_LABEL[it.action] || it.action}</span></td>` +
        `<td>${it.title ? `<div>${esc(it.title)}</div>` : ""}<div class="url">${esc(it.url)}</div></td>` +
        `<td>${it.tags.map((t) => `<span class="chip">${esc(t)}</span>`).join(" ")}</td>`;
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    box.appendChild(table);
    box.classList.remove("hidden");
  }

  function renderReport(r) {
    const box = $("import-report");
    box.className = "summary";
    box.innerHTML =
      `<strong>Import „${esc(r.name)}“ gestartet.</strong><br>` +
      `${r.created} Video(s) in die Warteschlange gestellt · ${r.tags_added} mit Tags ergänzt · ` +
      `${r.unchanged} unverändert` +
      (r.tags_created.length ? `<br>Neue Tags: ${r.tags_created.map(esc).join(", ")}` : "") +
      (r.errors.length
        ? `<ul>${r.errors.map((e) => `<li class="error">${esc(e.item)}: ${esc(e.message)}</li>`).join("")}</ul>`
        : "");
    box.classList.remove("hidden");
  }

  function renderImports() {
    const list = $("import-list");
    list.replaceChildren();
    if (!imports.length) {
      list.innerHTML = '<p class="hint">Noch keine Importe.</p>';
      return;
    }
    for (const imp of imports) {
      const item = document.createElement("div");
      item.className = "item import";

      const info = document.createElement("div");
      const title = document.createElement("div");
      title.className = "title";
      title.textContent = imp.name;
      const sub = document.createElement("div");
      sub.className = "sub";
      const done = imp.downloaded;
      const parts = [`${done} / ${imp.total} fertig`];
      if (imp.downloading) parts.push(`${imp.downloading} lädt`);
      if (imp.queued) parts.push(`${imp.queued} wartend`);
      if (imp.error) parts.push(`${imp.error} Fehler`);
      sub.textContent = parts.join(" · ") +
        ` · ${new Date(imp.created_at).toLocaleString("de-DE")}` +
        (imp.source ? ` · ${imp.source}` : "");
      const bar = document.createElement("div");
      bar.className = "progress-bar";
      for (const k of ["downloaded", "downloading", "error"]) {
        if (!imp[k] || !imp.total) continue;
        const seg = document.createElement("div");
        seg.className = `seg ${k}`;
        seg.style.width = `${(imp[k] / imp.total) * 100}%`;
        bar.appendChild(seg);
      }
      info.append(title, sub, bar);

      const actions = document.createElement("div");
      actions.className = "actions";
      const expanded = expandedImports.has(imp.id);
      actions.appendChild(button(expanded ? "Zuklappen" : "Details", "btn small", () => {
        if (expanded) expandedImports.delete(imp.id);
        else expandedImports.add(imp.id);
        renderImports();
      }));
      actions.appendChild(button("Aus Liste entfernen", "btn small", async () => {
        if (!confirm(`Import „${imp.name}“ aus der Übersicht entfernen? Videos bleiben erhalten.`)) return;
        await api(`/api/admin/imports/${imp.id}`, { method: "DELETE" });
        expandedImports.delete(imp.id);
        refreshAll();
      }));

      item.append(info, actions);

      if (expanded) {
        const details = document.createElement("div");
        details.className = "details";
        details.innerHTML = '<p class="hint">Lade…</p>';
        item.appendChild(details);
        api(`/api/admin/imports/${imp.id}/videos`).then((vids) => {
          details.replaceChildren();
          for (const v of vids) details.appendChild(importVideoRow(v));
        }).catch(() => {});
      }
      list.appendChild(item);
    }
  }

  function importVideoRow(v) {
    const row = document.createElement("div");
    row.className = "item";
    const info = document.createElement("div");
    const t = document.createElement("div");
    t.className = "title";
    t.textContent = v.title;
    const s = document.createElement("div");
    s.className = "sub";
    const badge = document.createElement("span");
    badge.className = `badge ${v.status}`;
    badge.textContent = STATUS_LABEL[v.status] || v.status;
    s.appendChild(badge);
    s.appendChild(document.createTextNode(v.status === "error" && v.error_msg ? v.error_msg : v.source_url));
    info.append(t, s);
    const actions = document.createElement("div");
    actions.className = "actions";
    if (v.status === "error") {
      actions.appendChild(button("Erneut", "btn small", async () => {
        await api(`/api/admin/videos/${v.id}/retry`, { method: "POST" });
        refreshAll();
      }));
    }
    row.append(info, actions);
    return row;
  }

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // ---------- Start ----------

  (async () => {
    try {
      const s = await fetch("/api/admin/session").then((r) => r.json());
      if (s.authenticated) showApp();
      else showLogin();
    } catch (_) {
      showLogin();
    }
  })();
})();
