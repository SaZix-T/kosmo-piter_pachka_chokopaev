"""
metrics.py — свод данных источников в линии, окна и рекомендацию.

Строго следует docs/FACTORS_AND_MODEL.md:
  И1  ND != 0                     — везде Optional[int], None = ND
  И2  значения могут быть интервалами (lo, hi)
  И3  три времени хранятся раздельно
  И4  два механизма, не суммируются
  И5  без двойного счёта (G только через δ и φ в радиационной линии)
  И6  траектория участвует в SAA и сближениях (SAA — stub до SGP4/IGRF)
  И7  строгий replay: cutoff по времени публикации
  И10 не смешивать observation/forecast/calculation
  И11 сравниваются окна одинаковой длительности
  И12 никаких заявлений о безопасности

Вход: результат sources.collect_for_window().
Выход: AnalysisResult для фронта (см. to_dict()).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple


ALGO_VERSION = "0.3.2"


# ============================================================
# PARAM — параметры модели. Калибровка только вне тестовых окон.
# ============================================================

PARAM: Dict[str, Any] = {
    # 5.2 SEP: пороги J10 (pfu) → уровень
    "sep_s1":     10,
    "sep_s2":    100,
    "sep_s3":   1000,
    "sep_low":     1,

    # 5.2 прогноз SEP
    "sep_decay_hours_lo": 3.0,
    "sep_decay_hours_hi": 12.0,
    "sep_forecast_horizon_h": 6.0,

    # 5.4 радиационная линия
    "delta_sep_min": 1,
    "delta_g_min":   2,
    "phi_g_min":     3,

    # 5.6 сближения
    "pc_thresholds": [1e-4, 1e-5, 1e-6],
    "conj_tca_tolerance_s": 300,

    # 5.8 сравнение
    "warning_level_min": 2,

    # 5.7 уверенность
    "conf_low_max":  3,
    "conf_medium_max": 6,

    # Baseline-экстраполяция
    "g_baseline_hours": 3,
}


# ============================================================
# Словари человекочитаемых названий
# ============================================================

LINE_TITLES = {
    "radiation":    "Радиационная обстановка",
    "conjunctions": "Сближения с космическим мусором",
    "g_effects":    "Геомагнитная буря (справочно)",
    "cme_context":  "Выброс корональной массы (контекст)",
    "saa":          "Южно-Атлантическая аномалия",
}

MECHANISM_LABELS = {
    "space_weather": "Космическая погода",
    "mmod":          "Микрометеороиды и мусор",
}

KIND_LABELS = {
    "observation": "Наблюдение",
    "forecast":    "Прогноз",
    "calculation": "Расчёт",
    "mixed":       "Смешанное",
}

KIND_SHORT = {
    "observation": "Н",
    "forecast":    "П",
    "calculation": "Р",
    "mixed":       "Σ",
}

CONFIDENCE_LABELS = {
    "low":    "Низкая",
    "medium": "Средняя",
    "high":   "Высокая",
    "nd":     "Не определена",
}

LEVEL_SHORT = {
    0: "Спокойно",
    1: "Слабо",
    2: "Умеренно",
    3: "Сильно",
    4: "Экстремально",
}

SEVERITY_LABELS = {
    "info":     "Информация",
    "minor":    "Незначительно",
    "major":    "Серьёзно",
    "critical": "Критично",
}

OUTCOME_LABELS = {
    "recommended":  "Рекомендуется",
    "equivalent":   "Равнозначные окна",
    "insufficient": "Недостаточно данных",
    "single":       "Единственное окно",
    "no_threats":   "Угроз не обнаружено",
}


# ============================================================
# Константы
# ============================================================

KIND_OBS  = "observation"
KIND_FCST = "forecast"
KIND_CALC = "calculation"

MECH_SW   = "space_weather"
MECH_MMOD = "mmod"


# ============================================================
# Типы
# ============================================================

@dataclass
class Segment:
    """Участок окна с одним уровнем."""
    start: datetime
    end: datetime
    level: int
    kind: str
    source: str
    note: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "start": _iso(self.start), "end": _iso(self.end),
            "level": self.level,
            "level_label": LEVEL_SHORT.get(self.level, f"Уровень {self.level}"),
            "kind": self.kind,
            "kind_label": KIND_LABELS.get(self.kind, self.kind),
            "kind_short": KIND_SHORT.get(self.kind, "?"),
            "source": self.source,
            "note": self.note,
        }


@dataclass
class Line:
    """Одна линия анализа.

    contributes=True  — учитывается в окнах и рекомендации.
    contributes=False — информационная карточка.
    """
    name: str
    mechanism: str
    contributes: bool
    level_lo: Optional[int]
    level_hi: Optional[int]
    confidence: str
    kind: str
    sources: List[str] = field(default_factory=list)
    limitations: str = ""
    note: Optional[str] = None
    segments: List[Segment] = field(default_factory=list)

    @property
    def is_nd(self) -> bool:
        return self.level_lo is None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "title": LINE_TITLES.get(self.name, self.name),
            "summary": _line_summary(self),
            "mechanism": self.mechanism,
            "mechanism_label": MECHANISM_LABELS.get(self.mechanism, self.mechanism),
            "contributes": self.contributes,
            "level_lo": self.level_lo,
            "level_hi": self.level_hi,
            "level_label": _level_label(self.level_lo, self.level_hi, self.is_nd),
            "is_nd": self.is_nd,
            "confidence": self.confidence,
            "confidence_label": CONFIDENCE_LABELS.get(self.confidence, self.confidence),
            "kind": self.kind,
            "kind_label": KIND_LABELS.get(self.kind, self.kind),
            "kind_short": KIND_SHORT.get(self.kind, "?"),
            "sources": list(self.sources),
            "limitations": self.limitations,
            "note": self.note,
            "segments": [s.to_dict() for s in self.segments],
        }


@dataclass
class Warning:
    source: str
    kind: str
    severity: str
    label: str
    start: datetime
    end: datetime
    value: Optional[float] = None
    unit: Optional[str] = None
    publication_time: Optional[datetime] = None
    validity_end: Optional[datetime] = None
    limitations: Optional[str] = None
    rule: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "kind": self.kind,
            "kind_label": KIND_LABELS.get(self.kind, self.kind),
            "kind_short": KIND_SHORT.get(self.kind, "?"),
            "severity": self.severity,
            "severity_label": SEVERITY_LABELS.get(self.severity, self.severity),
            "label": self.label,
            "start": _iso(self.start), "end": _iso(self.end),
            "value": self.value, "unit": self.unit,
            "publication_time": _iso(self.publication_time) if self.publication_time else None,
            "validity_end": _iso(self.validity_end) if self.validity_end else None,
            "limitations": self.limitations,
            "rule": self.rule,
        }


@dataclass
class WindowResult:
    id: int
    start: datetime
    end: datetime
    duration_minutes: int
    peak_level_lo: int
    peak_level_hi: int
    minutes_at_warning: int
    confidence: str
    lines: List[Line]
    warnings: List[Warning]
    plan_change: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "start": _iso(self.start), "end": _iso(self.end),
            "duration_minutes": self.duration_minutes,
            "peak_level_lo": self.peak_level_lo,
            "peak_level_hi": self.peak_level_hi,
            "peak_level_label": _level_label(
                self.peak_level_lo, self.peak_level_hi, False),
            "minutes_at_warning": self.minutes_at_warning,
            "confidence": self.confidence,
            "confidence_label": CONFIDENCE_LABELS.get(self.confidence, self.confidence),
            "lines": [l.to_dict() for l in self.lines],
            "warnings": [w.to_dict() for w in self.warnings],
            "plan_change": self.plan_change,
        }


@dataclass
class AnalysisResult:
    mode: str
    cutoff: Optional[datetime]
    start: datetime
    end: datetime
    lines_global: List[Line]
    info_cards: List[Line]
    windows: List[WindowResult]
    recommendation: Optional[dict]
    sources_status: Dict[str, Any]
    errors: Dict[str, str]
    skipped_live_only: List[str]
    algorithm_version: str
    created_at: datetime

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "mode_label": "Исторический" if self.mode == "historical" else "Текущий",
            "cutoff": _iso(self.cutoff) if self.cutoff else None,
            "start": _iso(self.start), "end": _iso(self.end),
            "lines_global": [l.to_dict() for l in self.lines_global],
            "info_cards": [l.to_dict() for l in self.info_cards],
            "windows": [w.to_dict() for w in self.windows],
            "recommendation": self.recommendation,
            "sources_status": self.sources_status,
            "errors": self.errors,
            "skipped_live_only": self.skipped_live_only,
            "algorithm_version": self.algorithm_version,
            "created_at": _iso(self.created_at),
        }


# ============================================================
# Helpers
# ============================================================

def _iso(x: Optional[datetime]) -> Optional[str]:
    if x is None:
        return None
    return x.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    """Универсальный парсер даты.

    Поддерживает ISO 8601, SOCRATES-формат и SWPC-формат без tz.
    """
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None

    try:
        cand = s
        if cand.endswith("Z"):
            cand = cand[:-1] + "+00:00"
        d = datetime.fromisoformat(cand)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    for fmt in (
        "%Y %b %d %H:%M:%S.%f",
        "%Y %b %d %H:%M:%S",
        "%Y %b %d %H:%M",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%b-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _overlap(a1: datetime, a2: datetime, b1: datetime, b2: datetime) -> bool:
    return a1 < b2 and a2 > b1


def _clamp(x: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, x))


def _level_at(segments: List[Segment], t: datetime) -> int:
    for s in segments:
        if s.start <= t < s.end:
            return s.level
    return 0


def _merge_minutes(intervals: List[Tuple[datetime, datetime]]) -> int:
    if not intervals:
        return 0
    intervals = sorted(intervals)
    merged = [intervals[0]]
    for s, e in intervals[1:]:
        cs, ce = merged[-1]
        if s <= ce:
            merged[-1] = (cs, max(ce, e))
        else:
            merged.append((s, e))
    return sum(int((e - s).total_seconds() // 60) for s, e in merged)


def _best_env(*envs: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Первый envelope с непустыми items; иначе первый без error; иначе первый."""
    non_empty = [e for e in envs if e and (e.get("items") or [])]
    if non_empty:
        return non_empty[0]
    non_error = [e for e in envs if e and not e.get("error")]
    if non_error:
        return non_error[0]
    return next((e for e in envs if e), None)


def _level_label(lo: Optional[int], hi: Optional[int], is_nd: bool) -> str:
    if is_nd or lo is None or hi is None:
        return "Нет данных"
    if lo == hi:
        return LEVEL_SHORT.get(lo, f"Уровень {lo}")
    lo_s = LEVEL_SHORT.get(lo, f"{lo}")
    hi_s = LEVEL_SHORT.get(hi, f"{hi}")
    return f"От «{lo_s.lower()}» до «{hi_s.lower()}»"


def _line_summary(line: "Line") -> str:
    """Короткое человекочитаемое описание состояния линии."""
    name = line.name

    if name == "radiation":
        if line.is_nd:
            return ("Нет свежих данных о радиации и нет предупреждений "
                    "от NOAA. Оценить радиационную обстановку нельзя.")
        hi = line.level_hi or 0
        if hi == 0:
            return "Поток протонов в норме, геомагнитный фон спокойный."
        if hi == 1:
            return ("Незначительное радиационное воздействие — поток "
                    "протонов слегка повышен или активна слабая буря.")
        if hi == 2:
            return "Умеренный радиационный риск. Работы возможны с оговорками."
        if hi == 3:
            return "Сильный радиационный риск. ВКД в этом окне нежелательна."
        return ("Экстремальный радиационный риск. Проводить ВКД в этом "
                "окне опасно.")

    if name == "conjunctions":
        if line.is_nd:
            return ("Сближения не проверены — источник недоступен. "
                    "Отсутствие данных не означает отсутствие угроз.")
        if (line.level_hi or 0) == 0:
            return ("Сближений с отслеживаемыми объектами на опасное "
                    "расстояние в окне нет.")
        return ("Обнаружены сближения с объектами. Смотрите детали "
                "ниже — там вероятность и дистанция.")

    if name == "g_effects":
        if line.is_nd:
            return "Данные о геомагнитной активности отсутствуют."
        return (f"Зафиксирована геомагнитная буря уровня G{line.level_hi}. "
                "Собственные эффекты (зарядка, торможение) справочно, "
                "в расчёт окна не идут.")

    if name == "cme_context":
        if line.is_nd:
            return ("Прогнозов прихода CME нет. Ожидать магнитных "
                    "возмущений не следует.")
        return ("Модель WSA-ENLIL оценивает приход выброса корональной "
                "массы. Точное время неизвестно — интервал ±7 часов.")

    if name == "saa":
        return ("Расчёт Южно-Атлантической аномалии пока не реализован — "
                "нужна модель магнитного поля IGRF-14 и пропагатор SGP4.")

    return ""


def _segment_summary(seg: "Segment") -> str:
    lvl = LEVEL_SHORT.get(seg.level, f"Уровень {seg.level}")
    return f"{lvl}: {seg.note or seg.source}"


# ============================================================
# 5.3 + 6.1 № 1. Kp → G
# ============================================================

def kp_to_g(kp: Optional[float]) -> Optional[int]:
    """Kp → уровень G (0..5). Округление до целого (третьи).

    0 = нет G-события (Kp < 5). None = данных нет.
    Особый случай: 9- (≈ 8.67) даёт G4, не G5.
    """
    if kp is None:
        return None
    if kp < 5:
        return 0
    if kp >= 9.0:
        return 5
    rounded = int(round(kp))
    if rounded == 9 and kp < 9.0:
        return 4
    if rounded >= 8:
        return 4
    if rounded == 7:
        return 3
    if rounded == 6:
        return 2
    return 1


# ============================================================
# 5.2 SEP
# ============================================================

def sep_level(j10: Optional[float], *, warning: bool = False) -> Optional[int]:
    """J10 (pfu) → уровень 0..4. ND если j10 отсутствует и нет warning."""
    if j10 is None and not warning:
        return None
    if j10 is None:
        return 1
    if j10 >= PARAM["sep_s3"]:
        return 4
    if j10 >= PARAM["sep_s2"]:
        return 3
    if j10 >= PARAM["sep_s1"]:
        return 2
    if j10 >= PARAM["sep_low"] or warning:
        return 1
    return 0


def build_sep_segments(
    env: Optional[Dict[str, Any]],
    w_start: datetime,
    w_end: datetime,
    *,
    warning_active: bool = False,
    forecast_hours: int = 6,
) -> List[Segment]:
    """Точки потока → сегменты по 5 минут.

    Baseline: последняя точка до w_start + экстраполяция на forecast_hours.
    """
    if not env or env.get("error") or not env.get("items"):
        return []

    points = [p for p in env["items"] if p.get("channel") == "P7"]
    if not points:
        return []

    points.sort(key=lambda p: p.get("time_utc") or "")
    src = env.get("source", "SEP")
    segs: List[Segment] = []

    baseline = None
    for p in points:
        t = _parse_iso(p.get("time_utc"))
        if t is None:
            continue
        if t <= w_start:
            baseline = (t, p)
        else:
            break

    if baseline:
        t0, p0 = baseline
        flux = p0.get("flux_pfu")
        lvl = sep_level(
            float(flux) if flux is not None else None,
            warning=warning_active,
        )
        if lvl is not None:
            ext_end = min(w_start + timedelta(hours=forecast_hours), w_end)
            if ext_end > w_start:
                note = (
                    f"baseline J10={flux:g} pfu, экстраполяция {forecast_hours} ч"
                    if flux is not None else "baseline: warning"
                )
                segs.append(Segment(
                    start=w_start, end=ext_end,
                    level=lvl, kind=KIND_FCST, source=src, note=note,
                ))

    for p in points:
        t = _parse_iso(p.get("time_utc"))
        if t is None or t < w_start or t > w_end:
            continue
        flux = p.get("flux_pfu")
        lvl = sep_level(
            float(flux) if flux is not None else None,
            warning=warning_active,
        )
        if lvl is None:
            continue
        seg_end = min(t + timedelta(minutes=5), w_end)
        segs.append(Segment(
            start=t, end=seg_end, level=lvl, kind=KIND_OBS, source=src,
            note=f"J10={flux:g} pfu" if flux is not None else "warning",
        ))

    return segs


# ============================================================
# G: Kp → сегменты
# ============================================================

def build_g_segments(
    env: Optional[Dict[str, Any]],
    w_start: datetime,
    w_end: datetime,
    *,
    forecast: bool = False,
    baseline_hours: int = 3,
) -> List[Segment]:
    """Kp-точки → сегменты по 3 часа с baseline."""
    if not env or env.get("error") or not env.get("items"):
        return []

    src = env.get("source", "G")
    kind = KIND_FCST if forecast else KIND_OBS
    all_pts: List[Tuple[datetime, Dict[str, Any]]] = []

    for p in env["items"]:
        if forecast:
            if p.get("observed", True):
                continue
        else:
            if p.get("observed") is False:
                continue
        t = _parse_iso(p.get("time_utc"))
        if t is None:
            continue
        all_pts.append((t, p))

    if not all_pts:
        return []

    all_pts.sort(key=lambda x: x[0])
    segs: List[Segment] = []

    baseline = None
    for t, p in all_pts:
        if t <= w_start:
            baseline = (t, p)
        else:
            break

    if baseline:
        t0, p0 = baseline
        kp_val = p0.get("kp") if "kp" in p0 else p0.get("Kp")
        g = kp_to_g(float(kp_val)) if kp_val is not None else None
        if g is not None:
            ext_end = min(t0 + timedelta(hours=baseline_hours), w_end)
            seg_start = max(t0, w_start)
            if ext_end > seg_start:
                segs.append(Segment(
                    start=seg_start, end=ext_end,
                    level=g, kind=kind, source=src,
                    note=f"baseline Kp={float(kp_val):.2f} → G{g}",
                ))

    for t, p in all_pts:
        if t < w_start or t > w_end:
            continue
        seg_end = min(t + timedelta(hours=3), w_end)
        kp_val = p.get("kp") if "kp" in p else p.get("Kp")
        g = kp_to_g(float(kp_val)) if kp_val is not None else None
        if g is None:
            continue
        segs.append(Segment(
            start=t, end=seg_end, level=g, kind=kind, source=src,
            note=f"Kp={float(kp_val):.2f} → G{g}",
        ))

    if not segs:
        return []
    merged: List[Segment] = [segs[0]]
    for s in segs[1:]:
        last = merged[-1]
        if s.start < last.end:
            if s.level > last.level:
                merged[-1] = s
        else:
            merged.append(s)
    return merged


# ============================================================
# 5.4 Радиационная линия
# ============================================================

def build_radiation_line(
    sep_segs: List[Segment],
    g_segs: List[Segment],
    w_start: datetime,
    w_end: datetime,
    *,
    sep_is_nd: bool,
    g_is_nd: bool,
    sample_min: int = 5,
) -> Line:
    """Радиационная линия: L(t) = min(4, max(SEP(t) + δ(t), φ(t)))."""
    if sep_is_nd:
        return Line(
            name="radiation", mechanism=MECH_SW, contributes=True,
            level_lo=None, level_hi=None,
            confidence="nd", kind=KIND_OBS,
            sources=[],
            limitations="SEP: нет свежих данных и нет предупреждения",
            note="SEP ND — уровень не определён (И1)",
        )

    if g_is_nd:
        levels = [s.level for s in sep_segs] or [0]
        return Line(
            name="radiation", mechanism=MECH_SW, contributes=True,
            level_lo=min(levels), level_hi=max(levels),
            confidence="low", kind=KIND_OBS,
            sources=sorted({s.source for s in sep_segs}),
            limitations="G ND, оценка только по SEP",
            note="G неизвестен — δ и φ не применены",
            segments=sep_segs,
        )

    rad_segs: List[Segment] = []
    cur: Optional[Segment] = None
    t = w_start
    step = timedelta(minutes=sample_min)
    while t < w_end:
        t_end = min(t + step, w_end)
        sep_lvl = _level_at(sep_segs, t)
        g_lvl = _level_at(g_segs, t)

        delta = 1 if (sep_lvl >= PARAM["delta_sep_min"]
                      and g_lvl >= PARAM["delta_g_min"]) else 0
        phi = 1 if (g_lvl >= PARAM["phi_g_min"]
                    and sep_lvl == 0) else 0
        lvl = min(4, max(sep_lvl + delta, phi))

        if cur is not None and cur.level == lvl:
            cur.end = t_end
        else:
            if cur is not None:
                rad_segs.append(cur)
            note_parts = []
            if delta:
                note_parts.append("δ=1")
            if phi:
                note_parts.append("φ=1")
            cur = Segment(
                start=t, end=t_end, level=lvl, kind=KIND_OBS,
                source="radiation",
                note="; ".join(note_parts) or None,
            )
        t = t_end
    if cur is not None:
        rad_segs.append(cur)

    levels = [s.level for s in rad_segs] or [0]
    lo, hi = min(levels), max(levels)

    note = None
    if any("φ=1" in (s.note or "") for s in rad_segs):
        note = "φ: G ≥ 3 при SEP = 0 — влияние G не оценено полностью"

    return Line(
        name="radiation", mechanism=MECH_SW, contributes=True,
        level_lo=lo, level_hi=hi,
        confidence="medium" if not sep_is_nd else "low",
        kind=KIND_OBS,
        sources=sorted({s.source for s in sep_segs + g_segs}),
        limitations="Уровень = max(SEP+δ, φ). G показан отдельной карточкой.",
        note=note,
        segments=rad_segs,
    )


# ============================================================
# 5.6 Сближения
# ============================================================

def build_conjunctions_line(
    env: Optional[Dict[str, Any]],
    w_start: datetime,
    w_end: datetime,
) -> Line:
    """L = 1 + число превышенных порогов из {1e-4, 1e-5, 1e-6}."""
    if not env or env.get("error"):
        return Line(
            name="conjunctions", mechanism=MECH_MMOD, contributes=True,
            level_lo=None, level_hi=None,
            confidence="nd", kind=KIND_OBS,
            sources=[env.get("source")] if env and env.get("source") else [],
            limitations=(
                "Источник не настроен или недоступен"
                if env and env.get("error")
                else (env.get("limitations") if env else "источник недоступен")
            ),
            note="ND: сближения не проверены (И1)",
        )

    items = env.get("items") or []
    src = env.get("source", "conjunctions")

    in_win = [c for c in items
              if (_parse_iso(c.get("tca")) is not None
                  and w_start <= _parse_iso(c.get("tca")) <= w_end)]

    tol = timedelta(seconds=PARAM["conj_tca_tolerance_s"])
    seen: List[Tuple[str, datetime]] = []
    uniq: List[Dict[str, Any]] = []
    for c in in_win:
        tca = _parse_iso(c.get("tca"))
        obj = str(c.get("sat_2_id") or c.get("sat_2_name") or "")
        dup = False
        for so, st in seen:
            if so == obj and abs((st - tca).total_seconds()) <= tol.total_seconds():
                dup = True
                break
        if not dup:
            seen.append((obj, tca))
            uniq.append(c)

    if not uniq:
        return Line(
            name="conjunctions", mechanism=MECH_MMOD, contributes=True,
            level_lo=0, level_hi=0,
            confidence="medium", kind=KIND_OBS,
            sources=[src],
            limitations=env.get("limitations") or "",
            note="Сближений в окне нет (источник ответил)",
        )

    pcs = [c.get("probability") for c in uniq
           if c.get("probability") is not None]
    if not pcs:
        return Line(
            name="conjunctions", mechanism=MECH_MMOD, contributes=True,
            level_lo=None, level_hi=None,
            confidence="low", kind=KIND_OBS,
            sources=[src],
            limitations="Pc отсутствует в записях",
            note="Сближения есть, но без Pc — уровень ND",
        )

    max_pc = max(pcs)
    exceeded = sum(1 for thr in PARAM["pc_thresholds"] if max_pc >= thr)
    level = _clamp(1 + exceeded, 1, 4)

    prod = 1.0
    for p in pcs:
        prod *= (1.0 - p)
    p_cum = 1.0 - prod

    return Line(
        name="conjunctions", mechanism=MECH_MMOD, contributes=True,
        level_lo=level, level_hi=level,
        confidence="medium" if max_pc >= 1e-5 else "low",
        kind=KIND_OBS,
        sources=[src],
        limitations=env.get("limitations") or "",
        note=f"max Pc={max_pc:.2e}, P_окна={p_cum:.3e}, событий={len(uniq)}",
    )


# ============================================================
# Информационные карточки
# ============================================================

def build_g_effects_card(g_segs: List[Segment]) -> Line:
    if not g_segs:
        return Line(
            name="g_effects", mechanism=MECH_SW, contributes=False,
            level_lo=None, level_hi=None,
            confidence="nd", kind=KIND_OBS,
            sources=[], limitations="Kp-данные отсутствуют",
            note="Собственные эффекты G: нет данных",
        )
    levels = [s.level for s in g_segs]
    return Line(
        name="g_effects", mechanism=MECH_SW, contributes=False,
        level_lo=min(levels), level_hi=max(levels),
        confidence="low", kind=KIND_OBS,
        sources=sorted({s.source for s in g_segs}),
        limitations="Зарядка и торможение не влияют на радиационную линию; вклад в сумму не учитывается (5.4)",
        note="Информационная карточка G",
        segments=g_segs,
    )


def build_cme_context_card(env: Optional[Dict[str, Any]]) -> Line:
    if not env or env.get("error") or not env.get("items"):
        return Line(
            name="cme_context", mechanism=MECH_SW, contributes=False,
            level_lo=None, level_hi=None,
            confidence="nd", kind=KIND_FCST,
            sources=[env.get("source")] if env and env.get("source") else [],
            limitations="DONKI CMEAnalysis недоступен",
            note="CME: контекст, уровня не даёт",
        )
    src = env.get("source", "CME")
    segs: List[Segment] = []
    for x in env["items"]:
        t_arr = _parse_iso(x.get("estimated_shock_arrival_time"))
        if t_arr is None:
            continue
        segs.append(Segment(
            start=t_arr - timedelta(hours=7),
            end=t_arr + timedelta(hours=7),
            level=0, kind=KIND_FCST, source=src,
            note=f"приход CME ±7 ч: {_iso(t_arr)}",
        ))
    return Line(
        name="cme_context", mechanism=MECH_SW, contributes=False,
        level_lo=None, level_hi=None,
        confidence="low", kind=KIND_FCST,
        sources=[src],
        limitations="Модельная оценка прихода; не превращать в уровень",
        note="CME: интервал прихода — контекст",
        segments=segs,
    )


def build_saa_stub() -> Line:
    return Line(
        name="saa", mechanism=MECH_SW, contributes=False,
        level_lo=None, level_hi=None,
        confidence="nd", kind=KIND_CALC,
        sources=["SGP4", "IGRF-14"],
        limitations="SGP4 и IGRF-14 не запускались; B_thr и контур SAA не выбраны",
        note="SAA: расчётный компонент не реализован",
    )


# ============================================================
# 5.7 Уверенность
# ============================================================

_CONF_ORDER = {"nd": -1, "low": 0, "medium": 1, "high": 2}


def make_confidence(actuality: int, closeness: int,
                    completeness: int, independence: int) -> str:
    total = actuality + closeness + completeness + independence
    if total <= PARAM["conf_low_max"]:
        return "low"
    if total <= PARAM["conf_medium_max"]:
        return "medium"
    return "high"


def min_confidence(*cs: str) -> str:
    if not cs:
        return "low"
    return min(cs, key=lambda c: _CONF_ORDER.get(c, -1))


# ============================================================
# 5.8 Окна
# ============================================================

def _line_for_window(line: Line, w_start: datetime, w_end: datetime) -> Line:
    segs = [s for s in line.segments
            if _overlap(s.start, s.end, w_start, w_end)]
    if not segs:
        return Line(
            name=line.name, mechanism=line.mechanism,
            contributes=line.contributes,
            level_lo=(0 if not line.is_nd else None),
            level_hi=(0 if not line.is_nd else None),
            confidence=line.confidence, kind=line.kind,
            sources=line.sources, limitations=line.limitations,
            note="нет пересекающихся сегментов", segments=[],
        )
    levels = [s.level for s in segs]
    return Line(
        name=line.name, mechanism=line.mechanism,
        contributes=line.contributes,
        level_lo=min(levels), level_hi=max(levels),
        confidence=line.confidence, kind=line.kind,
        sources=line.sources, limitations=line.limitations,
        note=line.note, segments=segs,
    )


def _minutes_at_or_above(lines: List[Line],
                         w_start: datetime, w_end: datetime,
                         threshold: int) -> int:
    intervals: List[Tuple[datetime, datetime]] = []
    for line in lines:
        if not line.contributes:
            continue
        for s in line.segments:
            if s.level >= threshold:
                intervals.append((max(s.start, w_start), min(s.end, w_end)))
    return _merge_minutes(intervals)


def analyze_windows(
    w_start: datetime,
    w_end: datetime,
    duration_hours: int,
    lines_global: List[Line],
    warnings: List[Warning],
    *,
    step_minutes: int = 30,
    max_windows: int = 8,
) -> List[WindowResult]:
    duration = timedelta(hours=duration_hours)
    windows: List[WindowResult] = []
    idx = 0
    t = w_start
    last_start = w_end - duration
    while t <= last_start:
        idx += 1
        we = t + duration
        wlines = [_line_for_window(l, t, we) for l in lines_global if l.contributes]
        lo = max((l.level_lo for l in wlines if l.level_lo is not None), default=0)
        hi = max((l.level_hi for l in wlines if l.level_hi is not None), default=0)
        minutes = _minutes_at_or_above(
            wlines, t, we, PARAM["warning_level_min"])
        conf = min_confidence(*[l.confidence for l in wlines]) if wlines else "low"
        wws = [w for w in warnings if _overlap(w.start, w.end, t, we)]
        windows.append(WindowResult(
            id=idx, start=t, end=we,
            duration_minutes=int(duration.total_seconds() // 60),
            peak_level_lo=lo, peak_level_hi=hi,
            minutes_at_warning=minutes,
            confidence=conf,
            lines=wlines,
            warnings=wws,
        ))
        t += timedelta(minutes=step_minutes)

    if len(windows) > max_windows:
        stride = len(windows) // max_windows
        windows = windows[::stride][:max_windows]
        for i, w in enumerate(windows, 1):
            w.id = i
    return windows


# ============================================================
# 5.8 Рекомендация
# ============================================================

def pick_recommendation(
    windows: List[WindowResult],
    lines_global: Optional[List[Line]] = None,
) -> Optional[dict]:
    """Пик → время под угрозой → уверенность. Исходы: recommended /
    equivalent / insufficient / single / no_threats.
    """
    if not windows:
        return None

    if lines_global is not None:
        contributing = [l for l in lines_global if l.contributes]
        if contributing and all(l.is_nd for l in contributing):
            return {
                "window_id": None,
                "start": None, "end": None,
                "outcome": "insufficient",
                "outcome_label": OUTCOME_LABELS["insufficient"],
                "reason": (
                    "все contributing-линии в состоянии ND — "
                    "данных для сравнения окон нет (И1)"
                ),
                "peak_level_lo": None,
                "peak_level_hi": None,
                "peak_level_label": "Нет данных",
                "minutes_at_warning": None,
                "confidence": "nd",
                "confidence_label": CONFIDENCE_LABELS["nd"],
            }

    all_clean = all(
        (w.peak_level_hi or 0) == 0 and (w.minutes_at_warning or 0) == 0
        for w in windows
    )
    if all_clean:
        best = sorted(windows, key=lambda w: w.start)[0]
        return {
            "window_id": best.id,
            "start": _iso(best.start), "end": _iso(best.end),
            "outcome": "no_threats",
            "outcome_label": OUTCOME_LABELS["no_threats"],
            "reason": (
                "во всех окнах значимых угроз не обнаружено; "
                "выбор ближайшего по времени — по удобству, не по обстановке (5.8)"
            ),
            "peak_level_lo": 0, "peak_level_hi": 0,
            "peak_level_label": LEVEL_SHORT[0],
            "minutes_at_warning": 0,
            "confidence": best.confidence,
            "confidence_label": CONFIDENCE_LABELS.get(best.confidence, best.confidence),
        }

    sorted_ws = sorted(
        windows,
        key=lambda w: (w.peak_level_hi, w.minutes_at_warning),
    )
    best = sorted_ws[0]
    second = sorted_ws[1] if len(sorted_ws) > 1 else None

    if second is None:
        return {
            "window_id": best.id,
            "start": _iso(best.start), "end": _iso(best.end),
            "outcome": "single",
            "outcome_label": OUTCOME_LABELS["single"],
            "reason": "единственное окно в периоде поиска",
            "peak_level_lo": best.peak_level_lo,
            "peak_level_hi": best.peak_level_hi,
            "peak_level_label": _level_label(
                best.peak_level_lo, best.peak_level_hi, False),
            "minutes_at_warning": best.minutes_at_warning,
            "confidence": best.confidence,
            "confidence_label": CONFIDENCE_LABELS.get(best.confidence, best.confidence),
        }

    overlap = not (
        best.peak_level_hi < second.peak_level_lo
        or second.peak_level_hi < best.peak_level_lo
    )
    if overlap:
        outcome = "equivalent"
        reason = (
            f"интервалы пиков пересекаются: "
            f"[{best.peak_level_lo}, {best.peak_level_hi}] vs "
            f"[{second.peak_level_lo}, {second.peak_level_hi}] — "
            "различие не признаётся"
        )
    elif best.minutes_at_warning < second.minutes_at_warning:
        outcome = "recommended"
        reason = (
            f"пик [{best.peak_level_lo}, {best.peak_level_hi}] ниже; "
            f"время под угрозой {best.minutes_at_warning} мин vs "
            f"{second.minutes_at_warning} мин"
        )
    elif best.minutes_at_warning > second.minutes_at_warning:
        outcome = "insufficient"
        reason = (
            f"пик [{best.peak_level_lo}, {best.peak_level_hi}] ниже, но "
            f"время под угрозой больше ({best.minutes_at_warning} vs "
            f"{second.minutes_at_warning} мин) — оснований для выбора мало"
        )
    else:
        outcome = "equivalent"
        reason = "окна равнозначны по пику и времени под угрозой"

    return {
        "window_id": best.id,
        "start": _iso(best.start), "end": _iso(best.end),
        "outcome": outcome,
        "outcome_label": OUTCOME_LABELS.get(outcome, outcome),
        "reason": reason,
        "peak_level_lo": best.peak_level_lo,
        "peak_level_hi": best.peak_level_hi,
        "peak_level_label": _level_label(
            best.peak_level_lo, best.peak_level_hi, False),
        "minutes_at_warning": best.minutes_at_warning,
        "confidence": best.confidence,
        "confidence_label": CONFIDENCE_LABELS.get(best.confidence, best.confidence),
    }


# ============================================================
# Warnings
# ============================================================

def _collect_warnings(
    sep_segs: List[Segment],
    g_segs: List[Segment],
    conj_line: Line,
    alerts_env: Optional[Dict[str, Any]],
) -> List[Warning]:
    out: List[Warning] = []

    for s in sep_segs:
        if s.level >= 2:
            out.append(Warning(
                source=s.source, kind=s.kind,
                severity=("critical" if s.level >= 4
                          else "major" if s.level == 3 else "minor"),
                label=f"Радиация: уровень {s.level}",
                start=s.start, end=s.end,
                unit="pfu", limitations=s.note,
                rule="J10 ≥ 10 pfu → уровень 2 (шкала NOAA S)",
            ))

    for s in g_segs:
        if s.level >= 3:
            out.append(Warning(
                source=s.source, kind=s.kind,
                severity=("critical" if s.level >= 4 else "minor"),
                label=f"Геомагнитная буря G{s.level}",
                start=s.start, end=s.end,
                unit="Kp", limitations=s.note,
                rule="Kp → G по таблице 5.3",
            ))

    for s in conj_line.segments:
        if s.level >= 1:
            out.append(Warning(
                source=s.source, kind=s.kind,
                severity="minor",
                label=s.note or "Сближение с объектом",
                start=s.start, end=s.end,
                unit="Pc",
                rule="Pc ≥ 1e-6 → уровень 1 (5.6)",
            ))

    if alerts_env and not alerts_env.get("error") and alerts_env.get("items"):
        for a in alerts_env["items"][:20]:
            valid_from = _parse_iso(a.get("valid_from")) or _parse_iso(a.get("issued"))
            valid_to = _parse_iso(a.get("valid_to"))
            if valid_from is None:
                continue
            msg = (a.get("message") or "").strip().splitlines()
            label = next((ln for ln in msg if ln.strip()), "Оповещение NOAA")[:120]
            out.append(Warning(
                source=alerts_env.get("source", "NOAA SWPC"),
                kind=KIND_FCST,
                severity="info",
                label=label,
                start=valid_from,
                end=valid_to or valid_from + timedelta(hours=6),
                publication_time=_parse_iso(a.get("issued")),
                validity_end=valid_to,
                limitations="Оповещение SWPC; три времени хранятся раздельно (И3)",
                rule="Оповещение SWPC → SEP ≥ 1",
            ))
    return out


# ============================================================
# Главная точка входа
# ============================================================

def analyze(
    data: Dict[str, Any],
    start: datetime,
    end: datetime,
    *,
    duration_hours: int = 6,
    cutoff: Optional[datetime] = None,
) -> AnalysisResult:
    """Строит линии, окна и рекомендацию по данным sources.collect_for_window."""
    mode = data.get("mode") or ("historical" if cutoff else "current")
    errors = dict(data.get("errors") or {})
    skipped = list(data.get("skipped_live_only") or [])

    # 1. Выбор актуальных envelope
    sep_env = _best_env(
        data.get("sep_goes_archive"),
        data.get("sep_swpc_protons"),
    )
    kp_env_obs = _best_env(
        data.get("gfz_kp"),
        data.get("swpc_kp"),
    )
    kp_env_fc = data.get("swpc_kp_forecast")

    alerts_env = data.get("swpc_alerts")
    warning_active = bool(
        alerts_env
        and not alerts_env.get("error")
        and (alerts_env.get("items") or [])
    )

    conj_env = _best_env(
        data.get("conjunctions"),
        data.get("socrates"),
    )

    # 2. Сегменты SEP и G
    sep_segs = build_sep_segments(
        sep_env, start, end,
        warning_active=warning_active,
    )

    if warning_active and not sep_segs:
        sep_segs = [Segment(
            start=start, end=end,
            level=1,
            kind=KIND_FCST,
            source=(alerts_env.get("source") if alerts_env else "NOAA SWPC"),
            note="активно оповещение — уровень 1 (эвристика 6.1 № 3)",
        )]

    sep_source_ok = sep_env is not None and sep_env.get("error") is None
    sep_is_nd = (not sep_source_ok and not warning_active)

    g_obs = build_g_segments(kp_env_obs, start, end, forecast=False)
    g_fc = build_g_segments(kp_env_fc, start, end, forecast=True)
    g_segs = g_obs + g_fc

    g_source_ok = (
        (kp_env_obs is not None and kp_env_obs.get("error") is None)
        or (kp_env_fc is not None and kp_env_fc.get("error") is None)
    )
    g_is_nd = not g_source_ok

    # 3. Contributing линии
    radiation = build_radiation_line(
        sep_segs, g_segs, start, end,
        sep_is_nd=sep_is_nd,
        g_is_nd=g_is_nd,
    )
    conjunctions = build_conjunctions_line(conj_env, start, end)
    saa = build_saa_stub()

    lines_global: List[Line] = [radiation, conjunctions]

    # 4. Информационные карточки
    info_cards: List[Line] = [
        build_g_effects_card(g_segs),
        build_cme_context_card(data.get("donki_cme")),
        saa,
    ]

    # 5. Warnings
    warnings = _collect_warnings(
        sep_segs, g_segs, conjunctions, alerts_env,
    )

    # 6. Окна и рекомендация
    windows = analyze_windows(
        start, end, duration_hours,
        lines_global, warnings,
    )
    recommendation = pick_recommendation(windows, lines_global)

    # 7. Статус источников
    sources_status: Dict[str, Any] = {}
    for key, env in data.items():
        if key in ("window", "errors", "mode", "cutoff", "skipped_live_only"):
            continue
        if not isinstance(env, dict):
            continue
        sources_status[key] = {
            "source": env.get("source"),
            "source_url": env.get("source_url"),
            "fetched_at": env.get("fetched_at"),
            "publication_time": env.get("publication_time"),
            "version": env.get("version"),
            "mode": env.get("mode"),
            "items_count": len(env.get("items") or []),
            "error": env.get("error"),
            "limitations": env.get("limitations"),
        }

    return AnalysisResult(
        mode=mode,
        cutoff=cutoff,
        start=start, end=end,
        lines_global=lines_global,
        info_cards=info_cards,
        windows=windows,
        recommendation=recommendation,
        sources_status=sources_status,
        errors=errors,
        skipped_live_only=skipped,
        algorithm_version=ALGO_VERSION,
        created_at=datetime.now(timezone.utc),
    )