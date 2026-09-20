(() => {
  const $ = (id) => document.getElementById(id);

  function qs(name) {
    const p = new URLSearchParams(window.location.search);
    return p.get(name);
  }

  const isoShort = (s) => {
    if (!s) return "—";
    try {
      return new Date(s).toISOString().replace("T", " ").slice(0, 16) + "Z";
    } catch (e) {
      return s;
    }
  };

  function renderAlert(msg, kind = "warning") {
    $("alert-block").innerHTML =
      `<div class="alert alert-${kind}">${msg}</div>`;
  }

  function renderMeta(calc) {
    const id = calc.id || calc.calculation_id || "—";
    const created = isoShort(calc.created_at);
    const mode = calc.mode === "historical" ? "Исторический" : "Текущий";
    $("meta-block").innerHTML = `
      <div>Расчёт: <code>${id}</code></div>
      <div>Создан: <code>${created}</code></div>
      <div>Режим: <code>${mode}</code></div>`;
  }

  function renderCards(sourcesStatus) {
    const entries = Object.entries(sourcesStatus || {})
      .sort((a, b) => a[0].localeCompare(b[0]));

    if (!entries.length) {
      $("table-block").innerHTML =
        `<div class="text-secondary">нет данных</div>`;
      return;
    }

    const cards = entries.map(([k, s]) => {
      const status = s.error
        ? `<span class="text-danger src-status" title="${escapeHtml(s.error)}">✗</span>`
        : (s.items_count > 0)
          ? `<span class="text-success src-status">✓</span>`
          : `<span class="text-warning src-status" title="Источник ответил, но записей нет">○</span>`;

      const modeLabel = s.mode === "historical" ? "истор." : "тек.";

      const pubTime = s.publication_time
        ? `<span class="badge bg-secondary" title="Время публикации">
             публ. ${isoShort(s.publication_time).slice(11, 16)}Z
           </span>`
        : "";

      return `
        <div class="src-card">
          <div class="src-card-head">
            <code class="src-key">${escapeHtml(k)}</code>
            ${status}
          </div>
          <div class="src-card-name">${escapeHtml(s.source || "—")}</div>
          <div class="src-card-meta">
            <span class="badge bg-secondary">${s.items_count ?? 0} зап.</span>
            <span class="badge bg-secondary">${modeLabel}</span>
            ${pubTime}
          </div>
          <div class="src-card-time">${isoShort(s.fetched_at)}</div>
        </div>`;
    }).join("");

    $("table-block").innerHTML = `
      <div class="src-grid">${cards}</div>
      <div class="small text-secondary mt-3">
        ✓ — данные получены · ○ — источник ответил, но записей нет · ✗ — ошибка
      </div>`;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function loadLatestCalcId() {
    const r = await fetch("/api/calculations");
    if (!r.ok) throw new Error("Не удалось получить список расчётов");
    const data = await r.json();
    const items = data.items || [];
    if (!items.length) return null;
    return items[0].id;
  }

  async function loadCalc(calcId) {
    const r = await fetch(`/api/analyze/${calcId}`);
    if (!r.ok) {
      const txt = await r.text();
      throw new Error(`Ошибка загрузки расчёта: ${r.status} ${txt}`);
    }
    return await r.json();
  }

  (async function init() {
    try {
      let calcId = qs("calc_id");
      if (!calcId) {
        calcId = await loadLatestCalcId();
        if (!calcId) {
          renderAlert("Расчётов пока нет. Запустите анализ на главной.", "info");
          $("table-block").innerHTML =
            `<a class="btn btn-outline-light" href="/">На главную</a>`;
          return;
        }
      }

      const calc = await loadCalc(calcId);
      renderMeta({ id: calcId, created_at: calc.created_at, mode: calc.mode });
      renderCards(calc.sources_status);
    } catch (e) {
      renderAlert(e.message || String(e), "danger");
    }
  })();
})();