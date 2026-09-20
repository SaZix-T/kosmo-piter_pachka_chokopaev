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
      <td class="small">
        ${isoShort(w.start)}<br>
        <span class="text-secondary">→ ${isoShort(w.end)}</span>
      </td>
      <td>${levelBadge(w.peak_level_lo, w.peak_level_hi, false, w.peak_level_label)}</td>
      <td class="text-center">${w.minutes_at_warning}</td>
      <td class="small text-secondary">${w.confidence_label || w.confidence}</td>
      <td class="text-center">${(w.warnings || []).length}</td>
    </tr>`).join("");

  $("windows-block").innerHTML = `
    <div class="table-wrap">
      <table class="table table-dark table-sm table-borderless align-middle mb-0">
        <thead><tr>
          <th>#</th>
          <th>Интервал</th>
          <th>Пик риска</th>
          <th class="text-center">Мин ≥ 2</th>
          <th>Уверенность</th>
          <th class="text-center">Оповещ.</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="small text-secondary mt-2 d-md-none">
      ← таблицу можно прокручивать →
    </div>`;
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
    const status = s.error
      ? `<span class="text-danger" title="${s.error}">✗</span>`
      : s.items_count > 0
        ? `<span class="text-success">✓</span>`
        : `<span class="text-warning" title="Источник ответил, но записей нет">○</span>`;

    return `
      <tr>
        <td class="small"><code>${k}</code></td>
        <td class="small">${s.source || "—"}</td>
        <td class="small text-end">${s.items_count}</td>
        <td class="small text-secondary">${s.mode === "historical" ? "истор." : "тек."}</td>
        <td class="small text-center">${status}</td>
      </tr>`;
  }).join("");

  $("sources-block").innerHTML = `
    <div class="table-wrap">
      <table class="table table-dark table-sm table-borderless mb-0">
        <thead><tr>
          <th>Ключ</th>
          <th>Источник</th>
          <th class="text-end">Записей</th>
          <th>Режим</th>
          <th class="text-center">Статус</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="small text-secondary mt-2">
      ✓ — данные получены · ○ — источник ответил, но записей нет · ✗ — ошибка
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
      renderGlobe(data);  // ← добавить
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
  // ---- 3D-карта МКС ----

let _globe = null;
let _globeScriptLoaded = false;

function loadScript(src) {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) return resolve();
    const s = document.createElement("script");
    s.src = src;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("CDN недоступен"));
    document.head.appendChild(s);
  });
}async function renderGlobe(data) {
  const host = document.getElementById("globe");
  if (!host) return;

  const loading = document.getElementById("globe-loading");
  const errorBox = document.getElementById("globe-error");
  const info = document.getElementById("globe-info");
  const badge = document.getElementById("globe-badge");

  try {
    if (!_globeScriptLoaded) {
      await loadScript("https://unpkg.com/globe.gl@2.32.4/dist/globe.gl.min.js");
      _globeScriptLoaded = true;
    }
    if (!window.Globe) throw new Error("Globe.gl не загрузился");

    const r = await fetch(`/api/iss/track?calc_id=${encodeURIComponent(calcId)}`);
    const track = await r.json();

    if (!r.ok || !track.points || !track.points.length) {
      throw new Error(track.error || "нет данных траектории");
    }

    await new Promise(res => requestAnimationFrame(res));
    await new Promise(res => requestAnimationFrame(res));

    const W = host.clientWidth || 800;
    const H = host.clientHeight || 460;

    // ---- Определяем окно, для которого рисуем ----
    // Приоритет: recommendation.window_id → первое окно → всё.
    let winStart = null, winEnd = null, winLabel = "всё окно поиска";
    const recId = data.recommendation?.window_id;
    const allWins = data.windows || [];

    if (recId != null) {
      const w = allWins.find(x => x.id === recId);
      if (w) {
        winStart = new Date(w.start).getTime();
        winEnd = new Date(w.end).getTime();
        winLabel = `окно #${w.id}`;
      }
    }
    if (winStart === null && allWins.length) {
      const w = allWins[0];
      winStart = new Date(w.start).getTime();
      winEnd = new Date(w.end).getTime();
      winLabel = `окно #${w.id}`;
    }

    // ---- Фильтр точек по окну ----
    let points = track.points;
    if (winStart !== null && winEnd !== null) {
      const filtered = track.points.filter(p => {
        const t = new Date(p.t).getTime();
        return t >= winStart && t <= winEnd;
      });
      if (filtered.length >= 2) points = filtered;
    }

    console.log("[globe] window:", winLabel,
                "points in window:", points.length,
                "of", track.points.length);

    loading.hidden = true;

    if (_globe) {
      host.innerHTML = "";
      _globe = null;
    }

    const g = Globe()(host)
      .width(W)
      .height(H)
      .globeImageUrl("//unpkg.com/three-globe/example/img/earth-night.jpg")
      .backgroundColor("rgba(0,0,0,0)")
      .showAtmosphere(true)
      .atmosphereColor("#7dd3fc")
      .atmosphereAltitude(0.18);

    g.pointOfView({ lat: 20, lng: 0, altitude: 2.3 }, 0);

    const first = points[0];
    const last = points[points.length - 1];

  // arcsData рисует great-circle дуги и корректно проходит антимеридиан.
  // Пропускаем только полностью совпадающие точки (что маловероятно).
  const arcs = [];
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i], b = points[i + 1];
    if (Math.abs(a.lat - b.lat) < 1e-6 && Math.abs(a.lon - b.lon) < 1e-6) {
      continue;
    }
    arcs.push({
      startLat: a.lat, startLng: a.lon,
      endLat:   b.lat, endLng:   b.lon,
    });
  }

    g.arcsData(arcs)
      .arcStartLat("startLat")
      .arcStartLng("startLng")
      .arcEndLat("endLat")
      .arcEndLng("endLng")
      .arcColor(() => "rgba(34, 211, 238, 0.95)")
      .arcStroke(0.5)
      .arcAltitude(0.02)
      .arcAltitudeAutoScale(0)
      .arcDashLength(1)
      .arcDashGap(0)
      .arcDashAnimateTime(0)
      .arcsTransitionDuration(0);

    // ---- Маркеры начала, конца и МКС ----
    g.pointsData([
      { lat: first.lat, lng: first.lon,
        color: "rgba(148, 163, 184, 0.9)", radius: 0.25,
        altitude: 0.015, label: "Начало окна" },
      { lat: last.lat, lng: last.lon,
        color: "rgba(168, 85, 247, 0.9)", radius: 0.25,
        altitude: 0.015, label: "Конец окна" },
      { lat: first.lat, lng: first.lon,
        color: "#a5f3fc", radius: 0.35,
        altitude: 0.025, label: "МКС" },
    ])
      .pointLat("lat")
      .pointLng("lng")
      .pointAltitude("altitude")
      .pointRadius("radius")
      .pointColor("color")
      .pointLabel("label")
      .pointResolution(16)
      .pointsMerge(false);

    g.ringsData([{ lat: first.lat, lng: first.lon }])
      .ringLat("lat")
      .ringLng("lng")
      .ringColor(() => (t) => `rgba(165, 243, 252, ${1 - t})`)
      .ringMaxRadius(2.4)
      .ringPropagationSpeed(2.8)
      .ringRepeatPeriod(1500);

    const controls = g.controls();
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.4;
    controls.enableZoom = true;
    controls.enablePan = false;
    controls.enableDamping = true;
    controls.minDistance = 200;
    controls.maxDistance = 800;
    controls.addEventListener("start", () => { controls.autoRotate = false; });
    controls.addEventListener("end", () => {
      setTimeout(() => { controls.autoRotate = true; }, 4000);
    });

    const onResize = () => {
      if (!_globe) return;
      const w = host.clientWidth, h = host.clientHeight;
      if (w && h) { _globe.width(w); _globe.height(h); }
    };
    window.addEventListener("resize", onResize);

    _globe = g;

    if (badge) {
      badge.textContent = track.mode === "historical" ? "Исторический TLE" : "Свежий TLE";
      badge.className = "badge " + (
        track.mode === "historical" ? "bg-warning text-dark" : "bg-success"
      );
    }

    if (info) {
      const durMin = points.length > 1
        ? Math.round((new Date(last.t) - new Date(first.t)) / 60000)
        : 0;
      info.innerHTML =
        `Траектория за ${winLabel}: ${points.length} точек · ${durMin} мин · ` +
        `${isoShort(first.t)} → ${isoShort(last.t)}` +
        (track.iss?.epoch_utc ? ` · эпоха TLE: ${isoShort(track.iss.epoch_utc)}` : "") +
        (track.source ? ` · источник: ${track.source}` : "");
    }
  } catch (err) {
    console.error("[globe]", err);
    if (loading) loading.hidden = true;
    if (errorBox) {
      errorBox.hidden = false;
      errorBox.textContent = "Карта недоступна: " + err.message;
    }
  }
}
})();