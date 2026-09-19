/* =========================================================
   app.js — анимации интерфейса, форма, редирект на результат.
   Никаких демо-данных: при ошибке показываем причину.
   ========================================================= */
(() => {
  'use strict';

  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---------------------------------------------------------
  // 1. Звёздное поле
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
          x: Math.random() * W, y: Math.random() * H,
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
        life: 0, maxLife: 60 + Math.random() * 30,
      });
    }

    function frame(now) {
      ctx.clearRect(0, 0, W, H);
      const scrollY = window.scrollY * 0.08;
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
      if (now - lastShoot > 9000 + Math.random() * 12000) {
        spawnShooting(); lastShoot = now;
      }
      for (let i = shooting.length - 1; i >= 0; i--) {
        const s = shooting[i];
        s.x += s.vx; s.y += s.vy; s.life++;
        const fade = 1 - s.life / s.maxLife;
        if (fade <= 0) { shooting.splice(i, 1); continue; }
        const tailX = s.x - s.vx * 12;
        const tailY = s.y - s.vy * 12;
        const grad = ctx.createLinearGradient(tailX, tailY, s.x, s.y);
        grad.addColorStop(0, 'rgba(180, 220, 255, 0)');
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
  // 2. Появление блоков
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
    els.forEach((el) => { el.classList.add('reveal-on-scroll'); io.observe(el); });
  }

  // ---------------------------------------------------------
  // 3. Тумблер режима
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
  // 4. Toast
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
  // 5. Форма → /api/analyze → redirect на /result/<id>
  // ---------------------------------------------------------
  function initForm() {
    const form = document.getElementById('analyzeForm');
    if (!form) return;

    const submitBtn = form.querySelector('button[type="submit"]');
    const status = document.getElementById('formStatus');

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

      setLoading(true);
      if (status) status.textContent = 'Запрос отправлен, ждём источники…';
      toast('Запускаю анализ…', 'info', 2500);

      try {
        const r = await fetch('/api/analyze', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const result = await r.json();
        if (!r.ok) throw new Error(result.error || `HTTP ${r.status}`);
        if (!result.calculation_id) throw new Error('Ответ без calculation_id');

        if (status) status.textContent = 'Готово, открываю результат…';
        window.location.href = `/result/${result.calculation_id}`;
      } catch (err) {
        console.error('[analyze]', err);
        if (status) status.textContent = 'Ошибка: ' + err.message;
        toast('Ошибка анализа: ' + err.message, 'error', 6000);
        setLoading(false);
      }
    });
  }

  // ---------------------------------------------------------
  // 6. Автозаполнение времени
  // ---------------------------------------------------------
  function initStartInput() {
    const startInput = document.getElementById('startInput');
    if (startInput && !startInput.value) {
      const d = new Date();
      d.setSeconds(0, 0);
      const pad = (n) => String(n).padStart(2, '0');
      startInput.value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }
  }

  function init() {
    initStars();
    initScrollReveals();
    initModeToggle();
    initForm();
    initStartInput();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();