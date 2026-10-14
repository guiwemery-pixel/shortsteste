(() => {
  const $ = (id) => document.getElementById(id);
  const form = $("job-form");
  const submit = $("submit");
  const formError = $("form-error");
  const durationInput = $("max_duration");
  const durationLabel = $("max_duration_label");
  const STAGES = ["metadata", "transcript", "download", "highlights", "render"];
  const METHODS = {
    claude: "Momentos escolhidos pela IA (Claude)",
    heatmap: "Momentos escolhidos pelos trechos mais revistos do YouTube",
    transcript: "Momentos escolhidos pela intensidade da fala",
    uniform: "Momentos distribuídos ao longo do vídeo",
  };
  let pollTimer = null;
  const renderedClips = new Set();

  const fmt = (seconds) => {
    const s = Math.max(0, Math.round(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = String(s % 60).padStart(2, "0");
    return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
  };

  const showError = (el, message) => {
    el.textContent = message || "";
    el.hidden = !message;
  };

  durationInput.addEventListener("input", () => {
    durationLabel.textContent = `${durationInput.value}s`;
  });

  // --- Tela dividida: escolha do vídeo de baixo -----------------------------
  const picker = $("background-picker");
  const backgroundsEl = $("backgrounds");
  const uploadStatus = $("upload-status");

  const selectedFormat = () => form.querySelector('input[name="format"]:checked').value;
  const syncPicker = () => {
    picker.hidden = selectedFormat() !== "split";
  };
  form.querySelectorAll('input[name="format"]').forEach((r) => r.addEventListener("change", syncPicker));

  async function loadBackgrounds(selectId) {
    const current = selectId || (form.querySelector('input[name="background"]:checked') || {}).value;
    let items = [];
    try {
      items = await (await fetch("/api/backgrounds")).json();
    } catch {
      return;
    }
    backgroundsEl.replaceChildren(
      ...items.map((item, index) => {
        const label = document.createElement("label");
        label.className = "bg-option";
        const input = document.createElement("input");
        input.type = "radio";
        input.name = "background";
        input.value = item.id;
        input.checked = current ? item.id === current : index === 0;
        label.appendChild(input);
        if (item.thumbnail_url) {
          const img = document.createElement("img");
          img.src = item.thumbnail_url;
          img.alt = "";
          img.loading = "lazy";
          label.appendChild(img);
        } else {
          const ph = document.createElement("div");
          ph.className = "bg-placeholder";
          label.appendChild(ph);
        }
        const name = document.createElement("span");
        name.textContent = item.name;
        label.appendChild(name);
        if (item.kind === "upload") {
          const remove = document.createElement("button");
          remove.type = "button";
          remove.className = "remove";
          remove.textContent = "✕";
          remove.title = "Remover este vídeo";
          remove.setAttribute("aria-label", `Remover ${item.name}`);
          remove.addEventListener("click", async (event) => {
            event.preventDefault();
            await fetch(`/api/backgrounds/${encodeURIComponent(item.id)}`, { method: "DELETE" });
            loadBackgrounds();
          });
          label.appendChild(remove);
        }
        return label;
      })
    );
    if (!form.querySelector('input[name="background"]:checked')) {
      const first = form.querySelector('input[name="background"]');
      if (first) first.checked = true;
    }
  }

  $("background-file").addEventListener("change", (event) => {
    const file = event.target.files[0];
    if (!file) return;
    const data = new FormData();
    data.append("file", file);
    data.append("name", file.name.replace(/\.[^.]+$/, ""));
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/backgrounds");
    uploadStatus.hidden = false;
    uploadStatus.textContent = "Enviando… 0%";
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) uploadStatus.textContent = `Enviando… ${Math.round((e.loaded / e.total) * 100)}%`;
    };
    xhr.onload = () => {
      let body = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {}
      if (xhr.status === 201) {
        uploadStatus.textContent = "Vídeo de fundo adicionado.";
        loadBackgrounds(body.id);
      } else {
        uploadStatus.textContent = body.detail || "Não foi possível enviar o vídeo.";
      }
      event.target.value = "";
    };
    xhr.onerror = () => {
      uploadStatus.textContent = "Falha de conexão ao enviar o vídeo.";
      event.target.value = "";
    };
    xhr.send(data);
  });

  loadBackgrounds();
  syncPicker();

  fetch("/api/health")
    .then((r) => r.json())
    .then((health) => {
      const badge = $("mode-badge");
      badge.hidden = false;
      badge.textContent = health.claude ? "IA ativa" : "Modo automático";
      badge.classList.toggle("ai", health.claude);
      badge.title = health.claude
        ? "Os momentos são escolhidos pelo Claude."
        : "Configure ANTHROPIC_API_KEY no servidor para a IA escolher os momentos.";
    })
    .catch(() => {});

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    showError(formError, "");
    const url = $("url").value.trim();
    if (!url) {
      showError(formError, "Cole o link de um vídeo do YouTube.");
      return;
    }
    const payload = {
      url,
      max_clips: Number($("max_clips").value),
      max_duration: Number(durationInput.value),
      format: selectedFormat(),
      captions: $("captions").checked,
      mirror: $("mirror").checked,
      remove_silence: $("remove_silence").checked,
    };
    const background = form.querySelector('input[name="background"]:checked');
    if (background) payload.background = background.value;
    submit.disabled = true;
    submit.textContent = "Enviando…";
    try {
      const response = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) {
        const detail = Array.isArray(data.detail) ? "Confira as opções e tente de novo." : data.detail;
        throw new Error(detail || "Não foi possível começar.");
      }
      history.replaceState(null, "", `?job=${data.id}`);
      startPolling(data.id);
    } catch (err) {
      showError(formError, err.message);
      resetButton();
    }
  });

  function resetButton() {
    submit.disabled = false;
    submit.textContent = "Gerar cortes";
  }

  function startPolling(jobId) {
    clearTimeout(pollTimer);
    $("results").hidden = true;
    $("download-all").hidden = true;
    $("clips").replaceChildren();
    renderedClips.clear();
    $("status").hidden = false;
    $("status").scrollIntoView({ behavior: "smooth", block: "start" });
    poll(jobId);
  }

  async function poll(jobId) {
    try {
      const response = await fetch(`/api/jobs/${jobId}`);
      if (response.status === 404) {
        showError($("status-error"), "Esse processamento expirou ou não existe.");
        resetButton();
        return;
      }
      const job = await response.json();
      renderJob(job);
      if (job.status === "queued" || job.status === "running") {
        pollTimer = setTimeout(() => poll(jobId), 1500);
      } else {
        resetButton();
      }
    } catch {
      pollTimer = setTimeout(() => poll(jobId), 3000);
    }
  }

  function renderJob(job) {
    const video = job.video;
    if (video) {
      $("video-title").textContent = video.title || "Vídeo do YouTube";
      $("video-meta").textContent = [video.channel, fmt(video.duration)].filter(Boolean).join(" · ");
      const thumb = $("video-thumb");
      if (video.thumbnail) {
        thumb.src = video.thumbnail;
        thumb.hidden = false;
      }
    }
    const percent = Math.round((job.progress || 0) * 100);
    $("progress-bar").style.width = `${percent}%`;
    document.querySelector(".progress").setAttribute("aria-valuenow", String(percent));
    $("status-message").textContent = job.message || "";

    const current = STAGES.indexOf(job.stage);
    document.querySelectorAll("#steps li").forEach((li, i) => {
      const done = job.status === "done" || (current > -1 && i < current);
      li.classList.toggle("done", done);
      li.classList.toggle("active", !done && i === current && job.status === "running");
    });

    const warnings = $("warnings");
    warnings.replaceChildren(
      ...(job.warnings || []).map((w) => {
        const li = document.createElement("li");
        li.textContent = w;
        return li;
      })
    );
    showError($("status-error"), job.status === "error" ? job.error : "");

    if (job.clips && job.clips.length) {
      $("results").hidden = false;
      const parts = [METHODS[job.method], job.transcript_source && `Fonte: ${job.transcript_source}`];
      $("results-meta").textContent = parts.filter(Boolean).join(" · ");
      job.clips.forEach((clip) => renderClip(job, clip));
    }
    const downloadAll = $("download-all");
    downloadAll.hidden = !(job.download_all_url && job.clips.length > 1);
    if (job.download_all_url) downloadAll.href = job.download_all_url;
  }

  async function copyText(button, text, label) {
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copiado!";
    } catch {
      button.textContent = "Não foi possível copiar";
    }
    setTimeout(() => (button.textContent = label), 1800);
  }

  function renderClip(job, clip) {
    const key = `${job.id}:${clip.file}`;
    if (renderedClips.has(key)) return;
    renderedClips.add(key);

    const node = $("clip-template").content.firstElementChild.cloneNode(true);
    const video = node.querySelector("video");
    video.poster = clip.thumbnail_url;
    video.addEventListener("error", () => {
      // Navegador sem suporte a H.264: mostra a capa e orienta a baixar.
      const img = document.createElement("img");
      img.className = "fallback-thumb";
      img.src = clip.thumbnail_url;
      img.alt = clip.title;
      const note = document.createElement("span");
      note.className = "fallback-note";
      note.textContent = "Prévia indisponível neste navegador. Baixe o MP4.";
      video.replaceWith(img);
      img.after(note);
    });
    video.src = clip.url;
    if (job.options.format === "original") node.querySelector(".player").classList.add("landscape");
    node.querySelector(".rank").textContent = `#${clip.rank}`;
    node.querySelector(".clip-title").textContent = clip.title;
    node.querySelector(".score").textContent = `${clip.score}/100`;
    const finalDuration = clip.final_duration || clip.duration;
    let timeText = `${fmt(clip.start)} → ${fmt(clip.end)} · ${Math.round(finalDuration)}s`;
    if (clip.silence_cuts > 0) {
      timeText += ` · ${clip.silence_cuts} ${clip.silence_cuts === 1 ? "silêncio cortado" : "silêncios cortados"}`;
    }
    node.querySelector(".clip-time").textContent = timeText;
    node.querySelector(".clip-hook").textContent = clip.hook || "";
    node.querySelector(".clip-reason").textContent = clip.reason || "";

    const options = (clip.caption_options || []).length ? clip.caption_options : clip.caption ? [clip.caption] : [];
    const postCaption = options[0] || [clip.title, clip.hook].filter(Boolean).join("\n");
    if (options.length) {
      node.querySelector(".post-caption").hidden = false;
      node.querySelector(".caption-options").replaceChildren(
        ...options.map((text) => {
          const li = document.createElement("li");
          const p = document.createElement("p");
          p.textContent = text;
          const button = document.createElement("button");
          button.type = "button";
          button.className = "button small-button";
          button.textContent = "Copiar";
          button.addEventListener("click", () => copyText(button, text, "Copiar"));
          li.append(p, button);
          return li;
        })
      );
    }

    const download = node.querySelector(".download");
    download.href = clip.download_url;
    download.setAttribute("download", `${clip.download_name}.mp4`);

    const srt = node.querySelector(".srt");
    if (clip.subtitles_url) {
      srt.href = clip.subtitles_url;
      srt.setAttribute("download", `${clip.download_name}.srt`);
    } else {
      srt.remove();
    }

    const source = node.querySelector(".source");
    if (job.video && job.video.id) {
      source.href = `https://www.youtube.com/watch?v=${encodeURIComponent(job.video.id)}&t=${Math.floor(clip.start)}s`;
    } else {
      source.remove();
    }

    const copy = node.querySelector(".copy");
    copy.addEventListener("click", () => copyText(copy, postCaption, "Copiar legenda do post"));

    $("clips").appendChild(node);
  }

  const existing = new URLSearchParams(location.search).get("job");
  if (existing && /^[a-f0-9]{32}$/.test(existing)) startPolling(existing);
})();
