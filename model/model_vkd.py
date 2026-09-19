"""Математическая модель оценки окна ВКД (исследовательский прототип).
Уровни 0-4; ND (None) = недостаточно данных и НЕ равно 0. Интервалы — кортежи (lo, hi).
Пороги и параметры помечены PARAM: их нужно обосновать источниками или откалибровать (Т5).
"""
from __future__ import annotations
import math
from collections import deque
from datetime import datetime, timedelta
ND = None

# ---------------- SEP ----------------
S_THRESH = (1, 10, 100, 1000)        # pfu, >=10 МэВ: уровни 1..4 (S1 = 10 pfu = уровень 2)

def level_sep_obs(j10, warning_active=False):
    """Уровень по интегральному потоку >=10 МэВ (pfu). Пороги вместо floor(log10) — без ошибок округления."""
    if j10 is ND:
        return ND
    lvl = sum(j10 >= t for t in S_THRESH)
    return max(lvl, 1) if warning_active else lvl

def sep_decay_flux_bounds(j_now, h_hours, tau_min, tau_max):
    """Спад: J(h) = J_now*exp(-h/tau); tau в [tau_min, tau_max] (PARAM). Возвращает (lo, hi) потока."""
    return j_now*math.exp(-h_hours/tau_min), j_now*math.exp(-h_hours/tau_max)

def sep_level_forecast_interval(j_now, h_hours, tau_min, tau_max, rising=False):
    lo_f, hi_f = sep_decay_flux_bounds(j_now, h_hours, tau_min, tau_max)
    lo, hi = level_sep_obs(lo_f), level_sep_obs(hi_f)
    if rising:                        # событие ещё нарастает: верхняя граница +1
        hi = min(4, level_sep_obs(j_now) + 1)
        lo = min(lo, hi)
    return lo, hi

def prob_event_in_window(p_by_date, start: datetime, hours: float):
    """P = 1 - prod (1-p_k)^(overlap_k/24): суточные вероятности по датам UTC, постоянная интенсивность внутри суток."""
    end = start + timedelta(hours=hours)
    surv, cur = 1.0, start
    while cur < end:
        day_end = datetime(cur.year, cur.month, cur.day) + timedelta(days=1)
        seg_end = min(end, day_end)
        p = p_by_date.get(cur.date())
        if p is None:
            return ND
        surv *= (1.0 - p) ** ((seg_end - cur).total_seconds()/86400.0)
        cur = seg_end
    return 1.0 - surv

def sep_beyond_horizon(p_window, p_star):
    """За горизонтом расчёта (>6 ч) — только вероятность из 3-дневного прогноза: интервал, уверенность низкая."""
    if p_window is ND:
        return ND
    return (0, 1) if p_window >= p_star else (0, 0)

def exposure_sep(j10, on_high_lat, dt_s):
    """Индикатор экспозиции (не доза): сумма J10 за время на высоких геомагнитных широтах. Порог широты ФИКСИРОВАН
    (без зависимости от Kp), чтобы влияние G не учитывалось дважды (только через delta_G)."""
    return sum(j*dt_s for j, h in zip(j10, on_high_lat) if h)

# ---------------- G ----------------
G_BOUNDS = (4.5, 5.5, 6.5, 7.5, 8.9)  # Kp (трети): G1..G5, округление к ближайшему целому (PARAM: конвенция)

def g_level(kp):
    return ND if kp is ND else sum(kp >= b for b in G_BOUNDS)      # 0..5

def l_g(kp):
    g = g_level(kp)
    return ND if g is ND else min(4, g)

# ---------------- Радиационная линия (SEP + G) ----------------
def f_rad(s, g):
    delta = 1 if (s >= 1 and g >= 2) else 0     # усиление SEP бурей
    phi = 1 if (g >= 3 and s == 0) else 0       # нижняя граница: сильная буря без SEP
    return min(4, max(s + delta, phi)), ("G_not_fully_assessed" if phi else None)

def rad_level(s, g):
    if s is ND:
        return ND, None
    if g is ND:
        return s, "G_ND"
    return f_rad(s, g)

def rad_interval(s_iv, g_iv):
    """Функция монотонна по обоим аргументам, поэтому границы подставляются напрямую."""
    if s_iv is ND or s_iv[0] is ND or s_iv[1] is ND:
        return ND, None
    if g_iv is ND or g_iv[0] is ND or g_iv[1] is ND:
        return s_iv, "G_ND"
    lo, f1 = f_rad(s_iv[0], g_iv[0]); hi, f2 = f_rad(s_iv[1], g_iv[1])
    return (lo, hi), (f1 or f2)

# ---------------- SAA ----------------
def saa_fraction(indicator):
    n = len(indicator)
    return sum(1 for x in indicator if x)/n if n else ND

def ecdf(ref):
    ref = sorted(ref)
    return lambda x: sum(1 for r in ref if r <= x)/len(ref)

def saa_level(f, ref_fractions, ref_dates=None, test_dates=None):
    """Относительный уровень. Опорное распределение берётся из периода, НЕ пересекающегося с тестовым (Т5)."""
    if ref_dates is not None and test_dates is not None:
        assert set(ref_dates).isdisjoint(test_dates), "утечка: опорный период пересекается с тестовым"
    if f is ND:
        return ND
    if f == 0:
        return 0
    return min(3, 1 + math.floor(3*ecdf(ref_fractions)(f)))

# ---------------- Сближения ----------------
CONJ_T = (1e-6, 1e-5, 1e-4)          # пороги ориентировочные (NASA: 1e-5 и 1e-4 для оценки оператора)

def level_conj(pc):
    return ND if pc is ND else 1 + sum(pc >= t for t in CONJ_T)

def p_total(pcs):
    s = 1.0
    for p in pcs:
        s *= (1.0 - p)
    return 1.0 - s

def dedupe_conjunctions(recs, tol=timedelta(minutes=5)):
    """recs: dicts {obj, tca, created, pc}. Одно событие = один объект и TCA в пределах tol; оставляется последняя версия."""
    out = []
    for r in sorted(recs, key=lambda x: (x["obj"], x["tca"])):
        if out and out[-1][-1]["obj"] == r["obj"] and r["tca"] - out[-1][-1]["tca"] <= tol:
            out[-1].append(r)
        else:
            out.append([r])
    return [max(g, key=lambda x: x["created"]) for g in out]

def conj_window(events, s, d_hours, sigma, t_in, t_post):
    """Оценка по окну [s, s+D): накопленная вероятность и минуты внутри зон конфликта. events после dedupe."""
    e = s + timedelta(hours=d_hours)
    hit, spans = [], []
    for ev in events:
        a, b = ev["tca"] - sigma - t_in, ev["tca"] + sigma + t_post
        if a < e and b > s:
            hit.append(ev)
            if level_conj(ev["pc"]) >= 2:
                spans.append((max(a, s), min(b, e)))
    spans.sort(); minutes, cur_end = 0.0, None
    for a, b in spans:
        if cur_end is None or a > cur_end:
            minutes += (b-a).total_seconds()/60; cur_end = b
        elif b > cur_end:
            minutes += (b-cur_end).total_seconds()/60; cur_end = b
    ptot = p_total([h["pc"] for h in hit])
    return {"level": level_conj(ptot) if hit else 0, "p_total": ptot, "t2_min": minutes, "n": len(hit)}

# ---------------- Временные ряды ----------------
def sliding_max(a, w):
    dq, out = deque(), []
    for i, x in enumerate(a):
        while dq and a[dq[-1]] <= x:
            dq.pop()
        dq.append(i)
        if dq[0] <= i-w:
            dq.popleft()
        if i >= w-1:
            out.append(a[dq[0]])
    return out

def window_sums(flags, w):
    pref = [0]
    for f in flags:
        pref.append(pref[-1] + (1 if f else 0))
    return [pref[i+w]-pref[i] for i in range(len(flags)-w+1)]

def union_flags(*levels_series, thr=2):
    return [any(l[i] >= thr for l in levels_series) for i in range(len(levels_series[0]))]

# ---------------- Уверенность ----------------
def freshness_score(age, tau):
    return 2 if age <= tau else (1 if age <= 3*tau else 0)

def confidence_label(c1, c2, c3, c4, pub_time_known=True):
    total = c1+c2+c3+c4
    label = "низкая" if total <= 3 else ("средняя" if total <= 6 else "высокая")
    return "низкая" if not pub_time_known else label

# ---------------- Сравнение окон ----------------
def _ivl(x):
    return (x, x) if not isinstance(x, tuple) else x

def compare_windows(cands, critical=("RAD", "CONJ"), plan_key=None):
    """cands: {key: {'M1': peak (число или (lo,hi)), 'M2': T2_union (мин, число или (lo,hi)), 'C': 0..8, 'nd': set линий с ND}}.
    Возвращает вердикт. Различия внутри неопределённости не считаются различиями."""
    ok = {k: v for k, v in cands.items() if not (set(v.get("nd", ())) & set(critical))}
    if not ok:
        return {"verdict": "insufficient", "recommended": None, "tied": [], "reason": "критическая линия без данных"}
    alive = set(ok)
    for m in ("M1", "M2"):
        best_hi = min(_ivl(ok[k][m])[1] for k in alive)
        alive = {k for k in alive if _ivl(ok[k][m])[0] <= best_hi}      # не строго хуже лучшего
        if len(alive) == 1:
            break
    if len(alive) > 1:
        cmax = max(ok[k]["C"] for k in alive)
        alive2 = {k for k in alive if ok[k]["C"] == cmax}
        alive = alive2
    if len(alive) == 1:
        (k,) = alive
        return {"verdict": "recommend", "recommended": k, "tied": [], "reason": "строгое преимущество"}
    if plan_key in alive:
        return {"verdict": "no_improvement", "recommended": plan_key, "tied": sorted(alive, key=str), "reason": "перенос ничего не улучшает"}
    return {"verdict": "equivalent", "recommended": None, "tied": sorted(alive, key=str), "reason": "различия в пределах неопределённости"}

# ---------------- Реплей ----------------
def replay_filter(records, cutoff, created_key="created"):
    """Только записи, опубликованные не позже момента отсечения. Запись без времени публикации непригодна."""
    used, bad = [], []
    for r in records:
        c = r.get(created_key)
        if c is None:
            bad.append(r)
        elif c <= cutoff:
            used.append(r)
    return used, bad

def latest_elset(elsets, t, cutoff):
    """Набор элементов с максимальной EPOCH <= t среди опубликованных до cutoff (CREATION_DATE <= cutoff)."""
    ok = [e for e in elsets if e["creation"] <= cutoff and e["epoch"] <= t]
    return max(ok, key=lambda e: e["epoch"]) if ok else ND

def tiebreak_by_plan_proximity(tied, plan_start):
    """Среди равнозначных по риску окон — ближайшее к плану. Это выбор по удобству, а не по риску; в интерфейсе так и писать."""
    return min(tied, key=lambda s: (abs(s - plan_start), s)) if tied else ND
