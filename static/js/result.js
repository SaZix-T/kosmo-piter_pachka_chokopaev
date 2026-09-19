(() => {
  const root = document.getElementById("result-root");
  const calcId = root.dataset.calcId;

  const $ = (id) => document.getElementById(id);

  const isoShort = (s) => {
    if (!s) return "—";
    return new Date(s).toISOString().replace("T", " ").slice(0, 16) + "Z";
  };

  const levelClass = (lvl) => {
    if (lvl === null || lvl === undefined) return "level-nd";
    return "level-" + Math.max(0, Math.min(4, lvl));
  };

  const levelBadge = (lo, hi, isNd, label) => {
    const text = label || (
      isNd ? "Нет данных"
      : lo === hi ? String(lo) : `${lo}–${hi}`
    );
    const cls = isNd ? "level-nd" : levelClass(hi);
    return `<span class="badge ${cls}">${text}</span>`;
  };

  const kindBadge = (short, label) =>
    `<span class="badge kind-${(short || "").toLowerCase()}"
            title="${label || ""}">${label || short || "?"}</span>`;

  // ---- блоки ----

  function renderHeader(data) {
    const cutoff = data.cutoff
      ? `<div>Отсечка данных: <code>${isoShort(data.cutoff)}</code> — учитываются только записи, опубликованные до этого момента</div>`
      : `<div class="text-secondary">Текущий режим: используются все свежие данные</div>`;

    const skipped = (data.skipped_live_only || []).length
      ? `<div class="text-warning small mt-1">Недоступны в этом режиме: ${data.skipped_live_only.join(", ")}</div>`
      : "";
    const errs = Object.keys(data.errors || {}).length
      ? `<div class="text-danger small mt-1">Проблемы с источниками: ${Object.keys(data.errors).join(", ")}</div>`
      : "";

    $("header-block").innerHTML = `
      <div class="row">
        <div class="col-md-7">
          <div>Окно поиска: <code>${isoShort(data.start)}</code> — <code>${isoShort(data.end)}</code></div>
          ${cutoff}
        </div>
        <div class="col-md-5 text-md-end">
          ${skipped}
          ${errs}
        </div>
      </div>`;

    const mode = data.mode === "historical" ? "Исторический" : "Текущий";
    $("mode-badge").textContent = mode;
    $("mode-badge").className = "badge " + (
      data.mode === "historical" ? "bg-warning text-dark" : "bg-success"
    );
    $("algo-version").textContent = "алгоритм " + data.algorithm_version;
  }

  function renderLine(line) {
    const head = `
      <div class="d-flex justify-content-between align-items-start mb-1">
        <div>
          <strong>${line.title || line.name}</strong>
          ${kindBadge(line.kind_short, line.kind_label)}
          ${line.is_nd
            ? levelBadge(null, null, true, "Нет данных")
            : levelBadge(line.level_lo, line.level_hi, false, line.level_label)}
          ${line.contributes ? "" : '<span class="badge bg-secondary">справочно</span>'}
        </div>
        <div class="small text-secondary" title="Уверенность оценки">
          Уверенность: ${line.confidence_label || line.confidence}
        </div>
      </div>`;

    const summary = line.summary
      ? `<div class="small mb-2" style="color:#a5f3fc">${line.summary}</div>`
      : "";
    const lim = line.limitations
      ? `<div class="small text-secondary mb-2">
           <i class="bi bi-exclamation-circle"></i> ${line.limitations}
         </div>` : "";
    const note = line.note
      ? `<div class="small text-secondary mb-2">${line.note}</div>` : "";

    const segs = (line.segments || []).slice(0, 30).map(s => `
      <div class="d-flex justify-content-between small py-1 border-bottom border-secondary border-opacity-25">
        <div>${isoShort(s.start)} → ${isoShort(s.end)}</div>
        <div>
          <span class="badge ${levelClass(s.level)}">${s.level_label || s.level}</span>
          ${kindBadge(s.kind_short, s.kind_label)}
          <span class="text-secondary">${s.source}</span>
        </div>
      </div>`).join("");

    const segBlock = segs
      ? `<details class="mt-2">
           <summary class="small text-secondary">Показать детали (${line.segments.length} интервалов)</summary>
           <div class="mt-2">${segs}</div>
         </details>`
      : "";

    return `<div class="mb-4 pb-3 border-bottom border-secondary border-opacity-25">
      ${head}${summary}${lim}${note}${segBlock}
    </div>`;
  }

  function renderLines(data) {
    const contributing = (data.lines_global || []).map(renderLine).join("");
    $("lines-block").innerHTML =
      contributing || `<div class="text-secondary">Нет данных</div>`;
  }

  function renderWindows(data) {
    const rows = (data.windows || []).map(w => `
      <tr>
        <td>#${w.id}</td>
        <td class="small">${isoShort(w.start)}<br>→ ${isoShort(w.end)}</td>
        <td>${levelBadge(w.peak_level_lo, w.peak_level_hi, false, w.peak_level_label)}</td>
        <td>${w.minutes_at_warning} мин</td>
        <td class="small text-secondary">${w.confidence_label || w.confidence}</td>
        <td>${(w.warnings || []).length}</td>
      </tr>`).join("");

    $("windows-block").innerHTML = `
      <table class="table table-dark table-sm table-borderless align-middle mb-0">
        <thead><tr>
          <th>#</th><th>Интервал</th><th>Пик риска</th>
          <th>Мин. на уровне 2+</th><th>Уверенность</th><th>Оповещений</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  function renderRecommendation(data) {
    const r = data.recommendation;
    if (!r) {
      $("recommendation-block").innerHTML =
        `<div class="text-secondary">Рекомендация отсутствует</div>`;
      return;
    }

    const outcomeBadge = r.outcome === "recommended"
      ? "bg-success"
      : r.outcome === "no_threats"
        ? "bg-info text-dark"
        : r.outcome === "insufficient"
          ? "bg-warning text-dark"
          : "bg-secondary";

    const title = r.window_id
      ? `Окно #${r.window_id}`
      : "Сравнение не выполнено";

    $("recommendation-block").innerHTML = `
      <div class="mb-2">
        <strong>${title}</strong>
        ${r.peak_level_lo != null
          ? levelBadge(r.peak_level_lo, r.peak_level_hi, false, r.peak_level_label)
          : ""}
      </div>
      ${r.start ? `<div class="small mb-2">${isoShort(r.start)} → ${isoShort(r.end)}</div>` : ""}
      <div class="mb-2">
        <span class="badge ${outcomeBadge}">${r.outcome_label || r.outcome}</span>
      </div>
      <div class="small text-secondary">${r.reason}</div>
      <div class="small mt-2">
        Уверенность: <strong>${r.confidence_label || r.confidence}</strong>
      </div>`;
  }

  function renderWarnings(data) {
    const all = (data.windows || []).flatMap(w => w.warnings || []);
    const seen = new Set();
    const uniq = all.filter(w => {
      const k = `${w.source}|${w.label}|${w.start}`;
      if (seen.has(k)) return false;
      seen.add(k); return true;
    });

    if (!uniq.length) {
      $("warnings-block").innerHTML =
        `<div class="text-secondary">Значимых предупреждений нет.</div>`;
      return;
    }

    const sevClass = {
      critical: "bg-danger",
      major:    "bg-warning text-dark",
      minor:    "bg-secondary",
      info:     "bg-info text-dark",
    };

    $("warnings-block").innerHTML = uniq.map(w => `
      <div class="mb-3 pb-3 border-bottom border-secondary border-opacity-25">
        <div class="d-flex justify-content-between mb-1">
          <div>
            <span class="badge ${sevClass[w.severity] || "bg-secondary"}">${w.severity_label || w.severity}</span>
            ${kindBadge(w.kind_short, w.kind_label)}
            <strong>${w.label}</strong>
          </div>
        </div>
        <div class="small">${isoShort(w.start)} → ${isoShort(w.end)}</div>
        ${w.publication_time ? `<div class="small text-secondary">Опубликовано: ${isoShort(w.publication_time)}</div>` : ""}
        ${w.validity_end ? `<div class="small text-secondary">Действует до: ${isoShort(w.validity_end)}</div>` : ""}
        ${w.rule ? `<div class="small text-info">Правило: ${w.rule}</div>` : ""}
        ${w.limitations ? `<div class="small text-secondary">${w.limitations}</div>` : ""}
      </div>`).join("");
  }

  function renderInfo(data) {
    const cards = (data.info_cards || []).map(renderLine).join("");
    $("info-block").innerHTML =
      cards || `<div class="text-secondary">Нет</div>`;
  }

  function renderSources(data) {
    const rows = Object.entries(data.sources_status || {}).map(([k, s]) => {
      const statusIcon = s.error
        ? `<span class="text-danger" title="${s.error}">✗</span>`
        : s.items_count > 0
          ? `<span class="text-success">✓</span>`
          : `<span class="text-warning" title="Источник ответил, но данных нет">○</span>`;

      return `
        <tr>
          <td class="small"><code>${k}</code></td>
          <td class="small">${s.source || "—"}</td>
          <td class="small text-end">${s.items_count}</td>
          <td class="small text-secondary">${s.mode === "historical" ? "истор." : "тек."}</td>
          <td class="small text-center">${statusIcon}</td>
        </tr>`;
    }).join("");

    $("sources-block").innerHTML = `
      <table class="table table-dark table-sm table-borderless mb-0">
        <thead><tr>
          <th>Ключ</th><th>Источник</th>
          <th class="text-end">Записей</th>
          <th>Режим</th><th class="text-center">Статус</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="small text-secondary mt-2">
        ✓ — данные получены · ○ — источник ответил, но записей нет · ✗ — ошибка (наведите для деталей)
      </div>`;
  }

  fetch(`/api/analyze/${calcId}`)
    .then(r => r.json())
    .then(data => {
      if (data.error) throw new Error(data.error);
      renderHeader(data);
      renderLines(data);
      renderWindows(data);
      renderRecommendation(data);
      renderWarnings(data);
      renderInfo(data);
      renderSources(data);
    })
    .catch(e => {
      root.innerHTML = `<div class="alert alert-danger">
        Не удалось загрузить расчёт: ${e.message}
      </div>`;
    });

  const ej = $("export-json");
  const ec = $("export-csv");
  if (ej) ej.href = `/api/export/${calcId}.json`;
  if (ec) ec.href = `/api/export/${calcId}.csv`;



    // ---------------------------------------------------------
  // 3D-глобус МКС
  // ---------------------------------------------------------
  function renderIssGlobe() {
    const container = document.getElementById("iss-globe");
    if (!container || typeof Globe === "undefined") return;

    fetch(`/api/iss-track/${calcId}`)
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          container.innerHTML =
            `<div class="text-secondary small p-3">Карта недоступна: ${data.error}</div>`;
          return;
        }

        const focus = data.focus;
        const track = (data.track || []).map(p => ({
          lat: p.lat, lng: p.lon, alt: p.alt_km / 6371,  // нормируем на радиус Земли
        }));

        const globe = Globe()
          .globeImageUrl("//unpkg.com/three-globe/example/img/earth-night.jpg")
          .bumpImageUrl("//unpkg.com/three-globe/example/img/earth-topology.png")
          .backgroundImageUrl("//unpkg.com/three-globe/example/img/night-sky.png")
          .atmosphereColor("#22d3ee")
          .atmosphereAltitude(0.18)
          (container);

        // Настройка камеры: смотрим на МКС
        globe.pointOfView({ lat: focus.lat -15, lng: focus.lon, altitude: 2.2 }, 0);

        // Трек МКС — анимированный пунктир
        globe
          .pathsData([{ points: track, color: "#22d3ee" }])
          .pathPoints("points")
          .pathPointLat(p => p.lat)
          .pathPointLng(p => p.lng)
          .pathPointAlt(p => p.alt)
          .pathColor("color")
          .pathStroke(1.5)
          .pathDashLength(0.35)
          .pathDashGap(0.25)
          .pathDashAnimateTime(6000)
          .pathTransitionDuration(0);

        // Точка МКС
        globe
          .pointsData([{ lat: focus.lat, lng: focus.lon, alt: focus.alt_km / 6371 }])
          .pointLat("lat")
          .pointLng("lng")
          .pointAltitude("alt")
          .pointRadius(0.35)
          .pointColor(() => "#facc15");

        // Кольцо вокруг МКС (пульсирует)
        globe.ringsData([{ lat: focus.lat, lng: focus.lon }])
          .ringLat("lat")
          .ringLng("lng")
          .ringColor(() => t => `rgba(250, 204, 21, ${1 - t})`)
          .ringMaxRadius(4)
          .ringPropagationSpeed(3)
          .ringRepeatPeriod(1200);

        // Медленное авто-вращение + перехват мыши
        const controls = globe.controls();
        controls.autoRotate = true;
        controls.enableZoom = true;
        controls.enablePan = true;

        document.getElementById("iss-meta").textContent =
          `${focus.lat.toFixed(2)}°, ${focus.lon.toFixed(2)}° · ${focus.alt_km.toFixed(0)} км`;

        document.getElementById("iss-caption").innerHTML =
          `TLE: ${data.tle_source || "—"} · ` +
          `момент: ${isoShort(focus.time)} · ` +
          `точек трека: ${data.track.length}`;

        // Ресайз
        window.addEventListener("resize", () => {
          globe.width(container.clientWidth);
          globe.height(container.clientHeight);
        });
      })
      .catch(e => {
        container.innerHTML =
          `<div class="text-secondary small p-3">Ошибка загрузки карты: ${e.message}</div>`;
      });
  }

  // Вызов в цепочке рендера результата
  fetch(`/api/analyze/${calcId}`)
    .then(r => r.json())
    .then(data => {
      if (data.error) throw new Error(data.error);
      renderHeader(data);
      renderLines(data);
      renderWindows(data);
      renderRecommendation(data);
      renderWarnings(data);
      renderInfo(data);
      renderSources(data);
      renderIssGlobe();   // ← добавили
    })
    .catch(e => {
      root.innerHTML = `<div class="alert alert-danger">
        Не удалось загрузить расчёт: ${e.message}
      </div>`;
    });
})();