/* =========================================================
   charts.js — анимированный таймлайн и сравнение окон.
   Экспортирует глобальный объект VKDCharts.
   ========================================================= */
(() => {
  'use strict';

  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function parseISO(s) {
    try { return new Date(s); } catch { return null; }
  }

  function fmtHM(d) {
    if (!d) return '—';
    const p = (n) => String(n).padStart(2, '0');
    return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
  }

  // ---------------------------------------------------------
  // Анимированный таймлайн окна
  // ---------------------------------------------------------
  function renderTimeline(track, ticks, rangeLabel, window_) {
    if (!track) return;

    if (!window_ || !window_.segments?.length) {
      track.innerHTML = `<div class="empty-state" style="position:relative;padding:1rem">
        <span class="text-muted small">Нет данных для таймлайна</span>
      </div>`;
      if (ticks) ticks.innerHTML = '';
      if (rangeLabel) rangeLabel.textContent = '—';
      return;
    }

    const segs = window_.segments;
    const start = parseISO(segs[0].start);
    const end = parseISO(segs[segs.length - 1].end);
    const total = end - start;

    track.innerHTML = '';
    segs.forEach((seg, i) => {
      const s = parseISO(seg.start);
      const e = parseISO(seg.end);
      const left = ((s - start) / total) * 100;
      const width = ((e - s) / total) * 100;

      const div = document.createElement('div');
      div.className = `timeline-segment ${seg.level}`;
      div.style.left = `${left}%`;
      div.style.width = prefersReduced ? `${width}%` : '0%';
      div.title = `${fmtHM(s)} — ${fmtHM(e)} · ${seg.level}`;
      track.appendChild(div);

      if (!prefersReduced) {
        // Плавное раскрытие сегментов слева направо
        requestAnimationFrame(() => {
          div.style.transition = `width .7s cubic-bezier(.22,.61,.36,1) ${i * 90}ms`;
          div.style.width = `${width}%`;
        });
      }
    });

    // Метки времени
    if (ticks) {
      const marks = 5;
      const parts = [];
      for (let i = 0; i <= marks; i++) {
        const t = new Date(start.getTime() + (total * i) / marks);
        parts.push(`<span>${fmtHM(t)}</span>`);
      }
      ticks.innerHTML = parts.join('');
    }

    if (rangeLabel) {
      rangeLabel.textContent = `${fmtHM(start)} — ${fmtHM(end)} UTC`;
    }
  }

  // ---------------------------------------------------------
  // Сравнение окон: карточки с анимированной полосой bad-минут
  // ---------------------------------------------------------
  function renderWindowsCompare(host, windows, recommendedId) {
    if (!host) return;

    if (!windows.length) {
      host.innerHTML = `<div class="empty-state">
        <i class="bi bi-columns-gap"></i>
        Нет окон-кандидатов.
      </div>`;
      return;
    }

    const maxBad = Math.max(1, ...windows.map((w) => w.minutes_bad || 0));

    host.innerHTML = windows.map((w, i) => {
      const isBest = w.id === recommendedId;
      const totalThreats = (w.threats_critical?.length || 0) + (w.threats_minor?.length || 0);
      const barW = ((w.minutes_bad || 0) / maxBad) * 100;

      return `
        <div class="compare-row glass-subtle ${isBest ? 'best' : ''} stagger"
             style="animation-delay:${i * 70}ms">
          <div data-label="Окно">
            <span class="fw-semibold">#${w.id}</span>
            ${isBest ? '<span class="badge-soft badge-kind ms-2">выбор</span>' : ''}
          </div>
          <div data-label="Начало">${fmtHM(parseISO(w.start))} UTC</div>
          <div data-label="bad">
            <span class="badge-soft badge-bad">${w.minutes_bad ?? 0} мин</span>
          </div>
          <div data-label="warn">
            <span class="badge-soft badge-warn">${w.minutes_warn ?? 0} мин</span>
          </div>
          <div data-label="eclipse">
            <span class="badge-soft badge-muted">${w.eclipse?.shadow_minutes ?? 0} мин</span>
          </div>
          <div data-label="Угрозы">
            ${totalThreats
              ? `<span class="badge-soft badge-warn">${totalThreats}</span>`
              : `<span class="badge-soft badge-good">нет</span>`}
          </div>
          <div class="compare-bar" style="--w:${barW}%"></div>
        </div>
      `;
    }).join('');

    // Запускаем анимацию полосы после вставки
    requestAnimationFrame(() => {
      host.querySelectorAll('.compare-bar').forEach((el) => {
        el.classList.add('animate');
      });
    });
  }

  window.VKDCharts = { renderTimeline, renderWindowsCompare };
})();