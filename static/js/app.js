/* =========================================================
   app.js — анимации интерфейса и обработка формы.
   Если бэкенд недоступен, используются демо-данные.
   ========================================================= */
(() => {
  'use strict';

  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---------------------------------------------------------
  // 1. Звёздное поле на canvas (с параллаксом и падающими звёздами)
  // ---------------------------------------------------------
  function initStars() {
    const layer = document.getElementById('starsLayer');
    if (!layer || prefersReduced) return;

    const canvas = document.createElement('canvas');
    canvas.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;display:block;';
    layer.appendChild(canvas);
    const ctx = canvas.getContext('2d');

    let W = 0, H = 0, dpr = Math.min(window.devicePixelRatio || 1, 2);
    const stars = [];
    const shooting = [];
    let lastShoot = performance.now();

    function resize() {
      W = layer.clientWidth;
      H = layer.clientHeight;
      canvas.width = W * dpr;
      canvas.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      stars.length = 0;
      const count = Math.min(160, Math.floor((W * H) / 9000));
      for (let i = 0; i < count; i++) {
        stars.push({
          x: Math.random() * W,
          y: Math.random() * H,
          r: Math.random() * 1.3 + 0.3,
          baseAlpha: 0.25 + Math.random() * 0.55,
          phase: Math.random() * Math.PI * 2,
          speed: 0.0008 + Math.random() * 0.0018,
          depth: 0.3 + Math.random() * 0.7,
        });
      }
    }

    function spawnShooting() {
      const fromLeft = Math.random() > 0.5;
      const angle = fromLeft
        ? Math.PI / 5 + Math.random() * 0.15
        : Math.PI - Math.PI / 5 - Math.random() * 0.15;
      const speed = 5 + Math.random() * 4;
      shooting.push({
        x: fromLeft ? -40 : W + 40,
        y: Math.random() * H * 0.5,
        vx: Math.cos(angle) * speed * (fromLeft ? 1 : -1) * -1,
        vy: Math.sin(angle) * speed,
        life: 0,
        maxLife: 60 + Math.random() * 30,
      });
    }

    function frame(now) {
      ctx.clearRect(0, 0, W, H);

      // Параллакс от скролла
      const scrollY = window.scrollY * 0.08;

      // Мигающие звёзды
      for (const s of stars) {
        const t = now * s.speed + s.phase;
        const twinkle = 0.7 + 0.3 * Math.sin(t);
        const alpha = s.baseAlpha * twinkle;
        const y = s.y - scrollY * s.depth;
        const yy = ((y % H) + H) % H;

        ctx.beginPath();
        ctx.arc(s.x, yy, s.r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(220, 230, 255, ${alpha})`;
        ctx.fill();
      }

      // Падающие звёзды
      if (now - lastShoot > 9000 + Math.random() * 12000) {
        spawnShooting();
        lastShoot = now;
      }

      for (let i = shooting.length - 1; i >= 0; i--) {
        const s = shooting[i];
        s.x += s.vx;
        s.y += s.vy;
        s.life++;
        const fade = 1 - s.life / s.maxLife;
        if (fade <= 0) { shooting.splice(i, 1); continue; }

        const tailX = s.x - s.vx * 12;
        const tailY = s.y - s.vy * 12;
        const grad = ctx.createLinearGradient(tailX, tailY, s.x, s.y);
        grad.addColorStop(0, `rgba(180, 220, 255, 0)`);
        grad.addColorStop(1, `rgba(220, 240, 255, ${0.9 * fade})`);

        ctx.strokeStyle = grad;
        ctx.lineWidth = 1.6;
        ctx.beginPath();
        ctx.moveTo(tailX, tailY);
        ctx.lineTo(s.x, s.y);
        ctx.stroke();
      }

      requestAnimationFrame(frame);
    }

    window.addEventListener('resize', resize);
    resize();
    requestAnimationFrame(frame);
  }

  // ---------------------------------------------------------
  // 2. Появление блоков при скролле
  // ---------------------------------------------------------
  function initScrollReveals() {
    const els = document.querySelectorAll('[data-reveal]');
    if (!els.length || prefersReduced) return;

    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) {
          e.target.classList.add('in-view');
          io.unobserve(e.target);
        }
      });
    }, { rootMargin: '0px 0px -10% 0px', threshold: 0.05 });

    els.forEach((el) => {
      el.classList.add('reveal-on-scroll');
      io.observe(el);
    });
  }

  // ---------------------------------------------------------
  // 3. Тумблер режима (current / historical)
  // ---------------------------------------------------------
  function initModeToggle() {
    const radios = document.querySelectorAll('input[name="mode"]');
    const cutoffBlock = document.getElementById('cutoffBlock');
    const labels = {
      current: document.getElementById('modeCurrent'),
      historical: document.getElementById('modeHistorical'),
    };

    function update() {
      const mode = document.querySelector('input[name="mode"]:checked')?.value || 'current';
      Object.entries(labels).forEach(([k, el]) => {
        if (!el) return;
        el.classList.toggle('btn-glass-primary', k === mode);
      });

      if (cutoffBlock) {
        if (mode === 'historical') {
          cutoffBlock.classList.remove('d-none');
          requestAnimationFrame(() => cutoffBlock.classList.add('visible'));
        } else {
          cutoffBlock.classList.remove('visible');
          setTimeout(() => cutoffBlock.classList.add('d-none'), 220);
        }
      }
    }

    radios.forEach((r) => r.addEventListener('change', update));
    update();
  }

  // ---------------------------------------------------------
  // 4. Анимированные счётчики
  // ---------------------------------------------------------
  function animateNumber(el, to, { duration = 700, decimals = 0, suffix = '' } = {}) {
    if (!el) return;
    const from = parseFloat(el.dataset.current || '0');
    const start = performance.now();
    el.dataset.current = String(to);

    function tick(now) {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      const value = from + (to - from) * eased;
      el.textContent = value.toFixed(decimals) + suffix;
      if (t < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  // ---------------------------------------------------------
  // 5. Всплывающие уведомления
  // ---------------------------------------------------------
  function toast(message, kind = 'info', timeout = 4200) {
    let host = document.getElementById('toastHost');
    if (!host) {
      host = document.createElement('div');
      host.id = 'toastHost';
      host.className = 'toast-host';
      document.body.appendChild(host);
    }
    const el = document.createElement('div');
    el.className = `toast-item toast-${kind}`;
    el.innerHTML = `
      <i class="bi ${
        kind === 'error' ? 'bi-exclamation-octagon' :
        kind === 'success' ? 'bi-check-circle' :
        'bi-info-circle'
      }"></i>
      <span>${message}</span>
    `;
    host.appendChild(el);
    requestAnimationFrame(() => el.classList.add('show'));

    setTimeout(() => {
      el.classList.remove('show');
      setTimeout(() => el.remove(), 300);
    }, timeout);
  }

  // ---------------------------------------------------------
  // 6. Рендер демо-данных (пока бэкенд не готов)
  // ---------------------------------------------------------
  function demoResult() {
    const now = new Date();
    const iso = (d) => d.toISOString().replace(/\.\d+Z$/, 'Z');
    const windows = [];
    for (let i = 0; i < 4; i++) {
      const s = new Date(now.getTime() + i * 90 * 60 * 1000);
      const e = new Date(s.getTime() + 6 * 3600 * 1000);
      const bad = [95, 20, 45, 130][i];
      const warn = [40, 15, 70, 55][i];
      windows.push({
        id: i + 1,
        start: iso(s),
        end: iso(e),
        duration_minutes: 360,
        weather_status: bad > 90 ? 'bad' : bad > 30 ? 'warn' : 'good',
        minutes_bad: bad,
        minutes_warn: warn,
        threats_critical: i === 3 ? ['Геомагнитная буря G3'] : [],
        threats_minor: i === 0 ? ['Повышенный Kp'] : [],
        eclipse: { shadow_minutes: [12, 8, 15, 20][i] },
        segments: buildDemoSegments(s, 360, bad),
      });
    }
    return {
      calculation_id: 'demo-' + Date.now().toString(36),
      algorithm_version: '0.2.0',
      recommendation: {
        window_id: 2,
        start: windows[1].start,
        end: windows[1].end,
        reason: 'Минимум угроз: 20 мин под bad, 15 мин под warn',
        score: 20 * 3 + 15,
      },
      windows,
      sources: [
        { name: 'celestrak_tle', source: 'Celestrak', items_count: 1, fetched_at: iso(now) },
        { name: 'spacetrack_history', source: 'Space-Track gp_history', items_count: 12, fetched_at: iso(now) },
        { name: 'spacetrack_cdm', source: 'Space-Track cdm_public', items_count: 2, fetched_at: iso(now), limitations: 'CDM публикуются с задержкой.' },
        { name: 'noaa_goes_sep', source: 'NOAA GOES SEP', items_count: 288, fetched_at: iso(now), units: 'pfu' },
      ],
      errors: {},
    };
  }

  function buildDemoSegments(start, durationMin, badMin) {
    // Простой набор сегментов: good → bad → warn → good
    const total = durationMin;
    const segs = [];
    let cursor = 0;
    const plan = [
      { w: 0.15, level: 'good' },
      { w: badMin / total, level: 'bad' },
      { w: 0.25, level: 'warn' },
      { w: 0.2, level: 'good' },
    ];
    const norm = plan.reduce((a, x) => a + x.w, 0);
    plan.forEach((p) => {
      const w = p.w / norm;
      const s = new Date(start.getTime() + cursor * 60 * 1000);
      cursor += w * total;
      const e = new Date(start.getTime() + cursor * 60 * 1000);
      segs.push({ start: s.toISOString(), end: e.toISOString(), level: p.level });
    });
    // последний сегмент дотянуть до конца
    segs[segs.length - 1].end = new Date(start.getTime() + total * 60 * 1000).toISOString();
    return segs;
  }

  // ---------------------------------------------------------
  // 7. Отправка формы
  // ---------------------------------------------------------
  async function fetchResult(payload) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 4000);
    try {
      const r = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: ctrl.signal,
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return await r.json();
    } finally {
      clearTimeout(t);
    }
  }

  function initForm() {
    const form = document.getElementById('analyzeForm');
    if (!form) return;

    const submitBtn = form.querySelector('button[type="submit"]');
    const status = document.getElementById('formStatus');
    const exportJson = document.getElementById('exportJsonBtn');
    const exportCsv = document.getElementById('exportCsvBtn');
    const refreshBtn = document.getElementById('refreshBtn');

    let lastResult = null;

    function setLoading(on) {
      if (!submitBtn) return;
      submitBtn.disabled = on;
      if (on) {
        submitBtn.dataset.originalText = submitBtn.innerHTML;
        submitBtn.innerHTML = `<span class="spinner-glass"></span> Идёт анализ…`;
      } else if (submitBtn.dataset.originalText) {
        submitBtn.innerHTML = submitBtn.dataset.originalText;
      }
    }

    form.addEventListener('submit', async (e) => {
      e.preventDefault();

      const data = new FormData(form);
      const mode = data.get('mode') || 'current';
      const payload = {
        mode,
        start: data.get('start') || null,
        duration: Number(data.get('duration') || 6),
        search_period: Number(data.get('search_period') || 24),
      };
      if (mode === 'historical') payload.requested_at = data.get('requested_at') || null;

      setLoading(true);
      if (status) status.textContent = 'Запрос отправлен…';

      let result;
      try {
        result = await fetchResult(payload);
        if (status) status.textContent = 'Расчёт получен.';
      } catch (err) {
        console.warn('[analyze] fallback to demo:', err.message);
        toast('Источник недоступен, показаны демо-данные', 'error');
        result = demoResult();
        if (status) status.textContent = 'Показаны демо-данные.';
      } finally {
        setLoading(false);
      }

      lastResult = result;
      renderResult(result);

      if (exportJson) exportJson.disabled = false;
      if (exportCsv) exportCsv.disabled = false;
    });

    if (refreshBtn) {
      refreshBtn.addEventListener('click', async () => {
        toast('Обновляю источники…', 'info');
        try {
          await fetch('/api/refresh', { method: 'POST' });
          toast('Кэш сброшен', 'success');
        } catch {
          toast('Бэкенд недоступен — оставлен локальный кэш', 'error');
        }
      });
    }

    if (exportJson) {
      exportJson.addEventListener('click', () => download(lastResult, 'json'));
    }
    if (exportCsv) {
      exportCsv.addEventListener('click', () => download(lastResult, 'csv'));
    }
  }

  function download(result, format) {
    if (!result) return;
    const id = result.calculation_id || 'calc';
    if (format === 'json') {
      const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' });
      triggerDownload(blob, `calc_${id}.json`);
    } else {
      const rows = [['window_id', 'start', 'end', 'bad_min', 'warn_min', 'threats']];
      (result.windows || []).forEach((w) => {
        rows.push([w.id, w.start, w.end, w.minutes_bad, w.minutes_warn,
          [...(w.threats_critical || []), ...(w.threats_minor || [])].join('; ')]);
      });
      const csv = rows.map((r) => r.map(escapeCsv).join(',')).join('\n');
      const blob = new Blob([csv], { type: 'text/csv' });
      triggerDownload(blob, `calc_${id}.csv`);
    }
  }

  function escapeCsv(v) {
    const s = String(v ?? '');
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }

  function triggerDownload(blob, name) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // ---------------------------------------------------------
  // 8. Рендер результата
  // ---------------------------------------------------------
  function renderResult(result) {
    renderRecommendation(result);
    renderWindows(result.windows || []);
    if (window.VKDCharts) {
      window.VKDCharts.renderTimeline(
        document.getElementById('timelineTrack'),
        document.getElementById('timelineTicks'),
        document.getElementById('timelineRange'),
        result.windows?.[0] || null,
      );
      window.VKDCharts.renderWindowsCompare(
        document.getElementById('windowsList'),
        result.windows || [],
        result.recommendation?.window_id,
      );
    }
    renderWarnings(result);
    renderSources(result);
  }

  function renderRecommendation(result) {
    const rec = result.recommendation;
    const title = document.getElementById('recTitle');
    const reason = document.getElementById('recReason');
    const score = document.getElementById('recScore');
    const calcId = document.getElementById('calcId');
    const calcTime = document.getElementById('calcTime');
    const algo = document.getElementById('algoVersion');

    if (calcId) calcId.textContent = result.calculation_id || '—';
    if (calcTime) calcTime.textContent = new Date().toLocaleTimeString();
    if (algo) algo.textContent = result.algorithm_version || '—';

    if (!rec) {
      title.textContent = 'Недостаточно данных';
      reason.textContent = 'Система не смогла выбрать предпочтительное окно.';
      score.textContent = 'score —';
      return;
    }

    title.textContent = `Окно #${rec.window_id}`;
    reason.textContent = rec.reason || '';
    score.textContent = `score ${Number(rec.score ?? 0).toFixed(1)}`;
    score.classList.remove('badge-muted');
    score.classList.add('badge-kind');
  }

  function renderWindows(windows) {
    // Оставляем логику отрисовки VKDCharts, здесь только заголовок
    // если charts.js не подключён.
    if (window.VKDCharts) return;
    const list = document.getElementById('windowsList');
    if (!list) return;
    list.innerHTML = windows.map((w) => `
      <div class="compare-row">
        <div data-label="Окно">#${w.id}</div>
        <div data-label="Начало">${fmtTime(w.start)}</div>
        <div data-label="bad">${w.minutes_bad ?? 0} мин</div>
        <div data-label="warn">${w.minutes_warn ?? 0} мин</div>
        <div data-label="eclipse">${w.eclipse?.shadow_minutes ?? 0} мин</div>
        <div data-label="Угрозы">${(w.threats_critical?.length || 0) + (w.threats_minor?.length || 0)}</div>
      </div>
    `).join('');
  }

  function renderWarnings(result) {
    const host = document.getElementById('warningsList');
    if (!host) return;

    const items = [];
    (result.windows || []).forEach((w) => {
      (w.threats_critical || []).forEach((label) => {
        items.push({ level: 'bad', label, window: w.id, kind: 'forecast', source: 'NOAA / Space-Track' });
      });
      (w.threats_minor || []).forEach((label) => {
        items.push({ level: 'warn', label, window: w.id, kind: 'observation', source: 'NOAA / Space-Track' });
      });
    });

    if (!items.length) {
      host.innerHTML = `
        <div class="empty-state">
          <i class="bi bi-shield-check"></i>
          Значимых предупреждений нет.
        </div>`;
      return;
    }

    host.innerHTML = items.map((it, i) => `
      <div class="warning-card glass glass-section level-${it.level} stagger" style="animation-delay:${i * 60}ms">
        <div class="d-flex justify-content-between align-items-start mb-1">
          <div class="fw-semibold">
            <i class="bi ${it.level === 'bad' ? 'bi-exclamation-triangle' : 'bi-info-circle'} me-2"></i>
            ${it.label}
          </div>
          <span class="badge-soft badge-${it.level}">${it.level}</span>
        </div>
        <div class="small text-muted">
          Окно #${it.window} ·
          <span class="badge-soft badge-kind">${it.kind}</span>
          · ${it.source}
        </div>
      </div>
    `).join('');
  }

  function renderSources(result) {
    const host = document.getElementById('sourcesList');
    if (!host) return;
    const list = result.sources || [];
    if (!list.length) {
      host.textContent = 'Нет данных.';
      return;
    }
    host.innerHTML = list.map((s) => `
      <div class="source-item">
        <div class="d-flex justify-content-between">
          <span class="fw-semibold">${s.source || s.name}</span>
          <span class="badge-soft ${s.error ? 'badge-bad' : 'badge-good'}">
            ${s.error ? 'ошибка' : 'ok'}
          </span>
        </div>
        <div class="small text-muted">
          ${s.items_count ?? 0} записей · ${s.units || ''}
        </div>
        ${s.limitations ? `<div class="small text-muted mt-1"><i class="bi bi-info-circle"></i> ${s.limitations}</div>` : ''}
      </div>
    `).join('');
  }

  // ---------------------------------------------------------
  // Утилиты
  // ---------------------------------------------------------
  function fmtTime(iso) {
    if (!iso) return '—';
    try {
      const d = new Date(iso);
      return d.toISOString().slice(11, 16);
    } catch { return iso; }
  }

  // ---------------------------------------------------------
  // Запуск
  // ---------------------------------------------------------
  function init() {
    initStars();
    initScrollReveals();
    initModeToggle();
    initForm();

    // Автоподстановка текущего времени
    const startInput = document.getElementById('startInput');
    if (startInput && !startInput.value) {
      const d = new Date();
      d.setSeconds(0, 0);
      const pad = (n) => String(n).padStart(2, '0');
      startInput.value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Экспорт для отладки
  window.VKDApp = { toast, animateNumber, demoResult };
})();