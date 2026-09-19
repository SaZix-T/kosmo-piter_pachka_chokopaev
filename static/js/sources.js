(() => {
  const root = document.getElementById('sources-root');
  const $ = (id) => document.getElementById(id);

  function qs(name) {
    const p = new URLSearchParams(window.location.search);
    return p.get(name);
  }

  const isoShort = (s) => {
    if (!s) return '—';
    try {
      return new Date(s).toISOString().replace('T', ' ').slice(0, 16) + 'Z';
    } catch (e) {
      return s;
    }
  };

  function renderAlert(msg, kind = 'warning') {
    document.getElementById('alert-block').innerHTML = `
      <div class="alert alert-${kind}">${msg}</div>`;
  }

  function renderMeta(calc) {
    const id = calc.id || calc.calculation_id || '—';
    const created = isoShort(calc.created_at);
    const mode = calc.mode || '—';
    document.getElementById('meta-block').innerHTML = `
      calc: <code>${id}</code><br>
      создано: <code>${created}</code><br>
      режим: <code>${mode}</code>`;
  }

  function renderTable(sourcesStatus) {
    const entries = Object.entries(sourcesStatus || {}).sort((a, b) => a[0].localeCompare(b[0]));
    if (!entries.length) {
      document.getElementById('table-block').innerHTML = `<div class="text-secondary">нет данных</div>`;
      return;
    }
    const rows = entries.map(([k, s]) => {
      const statusOk = !s.error;
      const statusCell = statusOk ? '<span class="text-success">✓</span>' : `<span class="text-danger" title="${s.error}">✗</span>`;
      return `
        <tr>
          <td class="small"><code>${k}</code></td>
          <td class="small">${s.source || '—'}</td>
          <td class="small">${s.items_count ?? '—'}</td>
          <td class="small">${isoShort(s.fetched_at)}</td>
          <td class="small">${isoShort(s.publication_time)}</td>
          <td class="small text-secondary">${s.mode || '—'}</td>
          <td class="small">${statusCell}</td>
        </tr>`;
    }).join('');

    document.getElementById('table-block').innerHTML = `
      <table class="table table-dark table-sm table-borderless align-middle mb-0">
        <thead><tr>
          <th>ключ</th>
          <th>источник</th>
          <th>items</th>
          <th>последний запрос</th>
          <th>публикация</th>
          <th>режим</th>
          <th>статус</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  async function loadLatestCalcId() {
    const r = await fetch('/api/calculations');
    if (!r.ok) throw new Error('Не удалось получить список расчётов');
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
      let calcId = qs('calc_id');
      if (!calcId) {
        calcId = await loadLatestCalcId();
        if (!calcId) {
          renderAlert('Расчётов пока нет. Запустите анализ на главной странице.', 'info');
          document.getElementById('table-block').innerHTML = `<a class="btn btn-outline-light" href="/">На главную</a>`;
          return;
        }
      }

      const calc = await loadCalc(calcId);
      renderMeta({ id: calcId, created_at: calc.created_at, mode: calc.mode });
      renderTable(calc.sources_status);
    } catch (e) {
      renderAlert(e.message || String(e), 'danger');
    }
  })();
})();
