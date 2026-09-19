(() => {
  const root = document.getElementById("result-root");
  const calcId = root.dataset.calcId;

  const $ = (id) => document.getElementById(id);

  // ---- утилиты ----

  const isoShort = (s) => {
    if (!s) return "—";
    return new Date(s).toISOString().replace("T", " ").slice(0, 16) + "Z";
  };

  const levelClass = (lvl) => {
    if (lvl === null || lvl === undefined) return "level-nd";
    if (lvl >= 4) return "level-4";
    if (lvl === 3) return "level-3";
    if (lvl === 2) return "level-2";
    if (lvl === 1) return "level-1";
    return "level-0";
  };

  const levelBadge = (lo, hi, isNd) => {
    if (isNd) return `<span class="badge level-nd">ND</span>`;
    if (lo === hi) return `<span class="badge ${levelClass(lo)}">${lo}</span>`;
    return `<span class="badge ${levelClass(hi)}">${lo}–${hi}</span>`;
  };

  const kindBadge = (kind) => {
    const map = {
      observation: ["Н", "kind-obs", "наблюдение"],
      forecast: ["П", "kind-fcst", "прогноз"],
      calculation: ["Р", "kind-calc", "расчёт"],
      mixed: ["Σ", "kind-mixed", "смешанное"],
    };
    const [short, cls, title] = map[kind] || ["?", "kind-mixed", kind];
    return `<span class="badge ${cls}" title="${title}">${short}</span>`;
  };

  const sourceLabel = (s) =>
    `<span class="text-secondary small">${s || "—"}</span>`;

  // ---- Cesium / ISS map ----

  let viewer = null;
  let issEntity = null;
  let orbitTle = null;

  function initViewer() {
    if (viewer || !window.Cesium) return;
    try {
      viewer = new Cesium.Viewer('iss-map', {
        imageryProvider: new Cesium.UrlTemplateImageryProvider({
          url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
          credit: '© OpenStreetMap contributors',
          tilingScheme: new Cesium.WebMercatorTilingScheme(),
          tileWidth: 256,
          tileHeight: 256,
          maximumLevel: 19,
        }),
        baseLayerPicker: false,
        geocoder: false,
        homeButton: false,
        sceneModePicker: false,
        navigationHelpButton: false,
        animation: false,
        timeline: false,
        infoBox: false,
        selectionIndicator: false,
        shouldAnimate: false,
        fullscreenButton: false,
      });
      viewer.scene.globe.enableLighting = true;
      viewer.scene.skyAtmosphere.show = true;
      // Free terrain provider (no Ion token)
      try {
        viewer.terrainProvider = new Cesium.CesiumTerrainProvider({
          url: 'https://assets.agi.com/stk-terrain/world'
        });
      } catch (terrErr) {
        // If terrain fails, fall back silently to ellipsoid terrain
        viewer.terrainProvider = new Cesium.EllipsoidTerrainProvider();
      }
    } catch (e) {
      const el = document.getElementById('iss-map');
      if (el) {
        el.innerHTML = `<div class="text-secondary">Ошибка инициализации карты: ${e.message}</div>`;
      }
    }
  }

  function computeIssLatLon(startIso, tle) {
    if (!window.satellite || !tle || !tle.line1 || !tle.line2 || !startIso) return null;
    try {
      const satrec = satellite.twoline2satrec(tle.line1, tle.line2);
      const date = new Date(startIso);
      const pv = satellite.propagate(satrec, date);
      if (!pv || !pv.position) return null;
      const gmst = satellite.gstime(date);
      const geo = satellite.eciToGeodetic(pv.position, gmst);
      const lat = satellite.degreesLat(geo.latitude);
      const lon = satellite.degreesLong(geo.longitude);
      const heightMeters = Number.isFinite(geo.height) ? geo.height * 1000 : 420000;
      return { lat, lon, heightMeters };
    } catch (e) {
      return null;
    }
  }

  function updateIssMarker(lat, lon, heightMeters) {
    if (!viewer || lat === undefined || lon === undefined) return;
    const pos = Cesium.Cartesian3.fromDegrees(lon, lat, heightMeters || 0);
    if (!issEntity) {
      issEntity = viewer.entities.add({
        position: pos,
        point: { pixelSize: 8, color: Cesium.Color.CYAN, outlineColor: Cesium.Color.WHITE, outlineWidth: 1 },
        label: { text: 'ISS', font: '12px sans-serif', fillColor: Cesium.Color.WHITE, showBackground: true, backgroundColor: Cesium.Color.BLACK.withAlpha(0.5), pixelOffset: new Cesium.Cartesian2(0, -20) },
      });
    } else {
      issEntity.position = pos;
    }
    viewer.camera.flyTo({ destination: Cesium.Cartesian3.fromDegrees(lon, lat, 2000000) });
  }

  function updateIssForStart(startIso) {
    // ensure viewer exists
    if (!viewer) initViewer();
    const p = computeIssLatLon(startIso, orbitTle);
    if (!p) {
      // no TLE or failed propagation — keep map as-is without overwriting
      return;
    }
    updateIssMarker(p.lat, p.lon, p.heightMeters);
  }

  // ---- блоки ----

  function renderHeader(data) {
    const cutoff = data.cutoff
      ? `<div>Cutoff: <code>${isoShort(data.cutoff)}</code></div>`
      : `<div class="text-secondary">Режим без отсечки</div>`;
    const skipped = (data.skipped_live_only || []).length
      ? `<div class="text-warning small">Пропущены live-only: ${data.skipped_live_only.join(", ")}</div>`
      : "";
    const errs = Object.keys(data.errors || {}).length
      ? `<div class="text-danger small">Ошибки источников: ${Object.keys(data.errors).join(", ")}</div>`
      : "";

    $("header-block").innerHTML = `
      <div class="row">
        <div class="col-md-6">
          <div>Окно поиска: <code>${isoShort(data.start)}</code> — <code>${isoShort(data.end)}</code></div>
          ${cutoff}
        </div>
        <div class="col-md-6 text-md-end">
          ${skipped}
          ${errs}
        </div>
      </div>`;

    $("mode-badge").textContent = data.mode === "historical" ? "Historical" : "Current";
    $("mode-badge").className = "badge " + (data.mode === "historical" ? "bg-warning" : "bg-success");
    $("algo-version").textContent = "algo " + data.algorithm_version;
  }

  function renderLine(line) {
    const head = `
      <div class="d-flex justify-content-between align-items-center mb-2">
        <div>
          <strong>${line.name}</strong>
          ${kindBadge(line.kind)}
          ${line.is_nd ? '' : levelBadge(line.level_lo, line.level_hi, false)}
          ${line.contributes ? '' : '<span class="badge bg-secondary">инфо</span>'}
        </div>
        <div class="small">${line.confidence}</div>
      </div>`;

    const lim = line.limitations
      ? `<div class="small mb-2">${line.limitations}</div>` : "";
    const note = line.note
      ? `<div class="small mb-2">${line.note}</div>` : "";

    const segs = (line.segments || []).slice(0, 40).map(s => `
      <div class="d-flex justify-content-between small py-1 border-bottom border-secondary border-opacity-25">
        <div>${isoShort(s.start)} → ${isoShort(s.end)}</div>
        <div>
          <span class="badge ${levelClass(s.level)}">${s.level}</span>
          ${kindBadge(s.kind)}
          <span class="text-secondary">${s.source}</span>
        </div>
      </div>`).join("");

    return `<div class="mb-3 pb-3 border-bottom border-secondary border-opacity-25">
      ${head}${lim}${note}
      ${segs ? `<div class="mt-2">${segs}</div>` : ''}
    </div>`;
  }

  function renderLines(data) {
    const contributing = (data.lines_global || []).map(renderLine).join("");
    $("lines-block").innerHTML = contributing || `<div class="text-secondary">нет данных</div>`;
  }

  function renderWindows(data) {
    const rows = (data.windows || []).map(w => `
      <tr data-window-id="${w.id}" data-start="${w.start}">
        <td>#${w.id}</td>
        <td class="small">${isoShort(w.start)}<br>→ ${isoShort(w.end)}</td>
        <td>${levelBadge(w.peak_level_lo, w.peak_level_hi, false)}</td>
        <td>${w.minutes_at_warning} мин</td>
        <td class="small text-secondary">${w.confidence}</td>
        <td>${(w.warnings || []).length}</td>
      </tr>`).join("");

    $("windows-block").innerHTML = `
      <table class="table table-dark table-sm table-borderless align-middle mb-0">
        <thead><tr>
          <th>#</th><th>Интервал</th><th>Пик</th>
          <th>≥2 (мин)</th><th>Уверенность</th><th>Warnings</th>
        </tr></thead>
        <tbody id="windows-tbody">${rows}</tbody>
      </table>`;

    // click handler to update ISS marker per window start
    const tb = document.getElementById('windows-tbody');
    if (tb) {
      tb.addEventListener('click', (ev) => {
        const tr = ev.target.closest('tr');
        if (!tr) return;
        const startIso = tr.getAttribute('data-start');
        updateIssForStart(startIso);
      });
    }
  }

  function renderRecommendation(data) {
    const r = data.recommendation;
    if (!r) {
      $("recommendation-block").innerHTML =
        `<div class="text-secondary">Рекомендация отсутствует</div>`;
      return;
    }
    const outcomeLabel = {
      recommended: "рекомендуется",
      equivalent: "равнозначные",
      insufficient: "недостаточно оснований",
      single: "единственное окно",
    }[r.outcome] || r.outcome;

    $("recommendation-block").innerHTML = `
      <div class="mb-2">
        <strong>Окно #${r.window_id}</strong>
        ${levelBadge(r.peak_level_lo, r.peak_level_hi, false)}
      </div>
      <div class="small mb-2">${isoShort(r.start)} → ${isoShort(r.end)}</div>
      <div class="mb-2"><span class="badge bg-info">${outcomeLabel}</span></div>
      <div class="small">${r.reason}</div>
      <div class="small mt-2">Уверенность: <strong>${r.confidence}</strong></div>`;
  }

  function renderWarnings(data) {
    const all = (data.windows || []).flatMap(w => w.warnings || []);
    // дедуп по (source,label,start)
    const seen = new Set();
    const uniq = all.filter(w => {
      const k = `${w.source}|${w.label}|${w.start}`;
      if (seen.has(k)) return false;
      seen.add(k); return true;
    });

    if (!uniq.length) {
      $("warnings-block").innerHTML =
        `<div class="text-secondary">Предупреждений нет</div>`;
      return;
    }

    const sevClass = { critical: "bg-danger", major: "bg-warning text-dark",
                       minor: "bg-secondary", info: "bg-info text-dark" };

    $("warnings-block").innerHTML = uniq.map(w => `
      <div class="mb-2 pb-2 border-bottom border-secondary border-opacity-25">
        <div class="d-flex justify-content-between">
          <div>
            <span class="badge ${sevClass[w.severity] || "bg-secondary"}">${w.severity}</span>
            ${kindBadge(w.kind)}
            <strong>${w.label}</strong>
          </div>
        </div>
        <div class="small">${isoShort(w.start)} → ${isoShort(w.end)}</div>
        ${w.publication_time ? `<div class="small text-secondary">публикация: ${isoShort(w.publication_time)}</div>` : ''}
        ${w.validity_end ? `<div class="small text-secondary">действует до: ${isoShort(w.validity_end)}</div>` : ''}
        ${w.rule ? `<div class="small text-info">правило: ${w.rule}</div>` : ''}
        ${w.limitations ? `<div class="small text-secondary">${w.limitations}</div>` : ''}
      </div>`).join("");
  }

  function renderInfo(data) {
    const cards = (data.info_cards || []).map(renderLine).join("");
    $("info-block").innerHTML = cards || `<div class="text-secondary">нет</div>`;
  }

  function renderSources(data) {
    const rows = Object.entries(data.sources_status || {}).map(([k, s]) => `
      <tr>
        <td class="small"><code>${k}</code></td>
        <td class="small">${s.source || "—"}</td>
        <td class="small">${s.items_count}</td>
        <td class="small text-secondary">${s.mode || "—"}</td>
        <td class="small ${s.error ? 'text-danger' : 'text-success'}">
          ${s.error ? "✗" : "✓"}
        </td>
      </tr>`).join("");

    $("sources-block").innerHTML = `
      <table class="table table-dark table-sm table-borderless mb-0">
        <thead><tr>
          <th>ключ</th><th>источник</th><th>items</th><th>mode</th><th></th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  // ---- запуск ----

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

      // init map and set initial position
      orbitTle = data.orbit_tle || null;
      initViewer();
      const rec = data.recommendation || {};
      const startIso = rec.start || ((data.windows || [])[0] || {}).start;
      if (startIso) {
        updateIssForStart(startIso);
      }
    })
    .catch(e => {
      root.innerHTML = `<div class="alert alert-danger">${e.message}</div>`;
    });

  $("export-json").href = `/api/export/${calcId}.json`;
  $("export-csv").href = `/api/export/${calcId}.csv`;
})();
