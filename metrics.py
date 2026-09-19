"""Сведение данных источников в факторы (Impact).

Отвечает за:
  - пороги (все в THRESHOLDS, аналитик их заменит)
  - классификацию (good / warn / bad)
  - разделение observation / forecast / calculation
  - сборку списка Impact для timelines

Никаких HTTP-запросов — всё это делает sources.py.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


# =========================================================
# ПОРОГИ — единственное место, куда смотрит аналитик.
# Комментарии с источником каждой шкалы оставлены,
# чтобы на защите было видно обоснование.
# =========================================================

THRESHOLDS: Dict[str, Any] = {
    # NOAA S-шкала (solar radiation storm), по каналу P7 (>10 MeV), pfu
    # Источник: https://www.swpc.noaa.gov/noaa-scales-explanation
    "sep": {
        "warn": 10,       # S1
        "bad":  1000,     # S3 и выше
        "unit": "pfu",
    },

    # Вероятность столкновения из CDM (PC)
    # Источник: практика ESA/NASA, пороги для принятия решений
    "cdm": {
        "warn": 1e-5,
        "bad":  1e-4,
        "unit": "probability",
    },

    # ZHR метеороидного потока
    # Источник: IMO, качественная градация
    "meteor": {
        "warn": 20,
        "bad":  100,
        "unit": "ZHR",
    },
}


# =========================================================
# Impact — единая структура фактора
# =========================================================

@dataclass
class Impact:
    start: datetime
    end: datetime
    level: str                    # good | warn | bad
    kind: str                     # observation | forecast | calculation
    mechanism: str                # space_weather | conjunction | meteor | orbit
    source: str
    source_url: Optional[str] = None
    publication_time: Optional[datetime] = None
    value: Optional[float] = None
    unit: Optional[str] = None
    confidence: Optional[str] = None    # high | medium | low
    threat_severity: Optional[str] = None   # critical | minor
    threat_label: Optional[str] = None
    limitations: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for k in ("start", "end", "publication_time"):
            if d[k]:
                d[k] = d[k].astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return d


# =========================================================
# Утилиты
# =========================================================

_RANK = {"good": 0, "warn": 1, "bad": 2}
_LEVELS = ("good", "warn", "bad")


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _worst(*levels: str) -> str:
    worst = max((_RANK.get(l, 0) for l in levels), default=0)
    return _LEVELS[worst]


def _classify(value: float, thresholds: Dict[str, Any]) -> str:
    if value >= thresholds["bad"]:
        return "bad"
    if value >= thresholds["warn"]:
        return "warn"
    return "good"


def _overlap(i: Impact, start: datetime, end: datetime) -> bool:
    return i.start < end and i.end > start


# =========================================================
# Конвертеры: source envelope -> list[Impact]
# =========================================================

def sep_to_impacts(env: Optional[Dict[str, Any]]) -> List[Impact]:
    """SEP proton flux (GOES L2 avg5m) → интервалы повышенной радиации.

    S-шкала NOAA считается по каналу P7 (>10 MeV). Интегральный >500 MeV
    используется как вспомогательный признак для confidence.
    """
    if not env or env.get("error") or not env.get("items"):
        return []

    # Только P7 и только diff-канал для классификации
    points = [
        p for p in env["items"]
        if p.get("kind") == "diff" and p.get("channel") == "P7"
    ]
    if not points:
        return []

    points.sort(key=lambda p: p["time_utc"])
    thr = THRESHOLDS["sep"]

    impacts: List[Impact] = []
    current: Optional[Dict[str, Any]] = None

    for p in points:
        flux = float(p["flux_pfu"])
        level = _classify(flux, thr)
        t = _parse_iso(p["time_utc"])

        if level == "good":
            if current:
                impacts.append(_close_sep(current, t, env, thr))
                current = None
            continue

        if current is None:
            current = {"start": t, "peak": flux, "level": level}
        else:
            current["peak"] = max(current["peak"], flux)
            current["level"] = _worst(current["level"], level)
            current["end"] = t

    if current:
        impacts.append(_close_sep(current, current.get("end", current["start"]), env, thr))

    return impacts


def _close_sep(cur: Dict[str, Any], end: datetime, env: Dict[str, Any],
               thr: Dict[str, Any]) -> Impact:
    # Если у интервала нет явного конца — растянем на 30 минут
    end = cur.get("end") or (cur["start"] + timedelta(minutes=30))
    severity = "critical" if cur["level"] == "bad" else "minor"
    return Impact(
        start=cur["start"],
        end=end,
        level=cur["level"],
        kind="observation",
        mechanism="space_weather",
        source=env.get("source", "NOAA GOES"),
        source_url=env.get("source_url"),
        publication_time=_parse_iso(env["fetched_at"]) if env.get("fetched_at") else None,
        value=cur["peak"],
        unit=thr["unit"],
        confidence="high",
        threat_severity=severity,
        threat_label=f"SEP {cur['level'].upper()} (P7)",
        limitations=env.get("limitations"),
    )


def cdm_to_impacts(env: Optional[Dict[str, Any]]) -> List[Impact]:
    """CDM (сближения) → интервалы ±30 мин вокруг TCA."""
    if not env or env.get("error") or not env.get("items"):
        return []

    thr = THRESHOLDS["cdm"]
    impacts: List[Impact] = []

    for c in env["items"]:
        pc = c.get("probability")
        miss = c.get("miss_distance_km")
        tca_s = c.get("tca")
        if not tca_s:
            continue

        # Если PC нет — оцениваем по miss distance (грубая эвристика)
        if pc is None and miss is not None:
            level = "bad" if miss < 0.5 else "warn" if miss < 5 else "good"
        elif pc is not None:
            level = _classify(pc, thr)
        else:
            continue

        if level == "good":
            continue

        tca = _parse_iso(tca_s)
        impacts.append(Impact(
            start=tca - timedelta(minutes=30),
            end=tca + timedelta(minutes=30),
            level=level,
            kind="forecast",
            mechanism="conjunction",
            source=env.get("source", "Space-Track"),
            source_url=env.get("source_url"),
            publication_time=_parse_iso(env["fetched_at"]) if env.get("fetched_at") else None,
            value=pc,
            unit=thr["unit"],
            confidence="high" if pc and pc >= thr["bad"] else "medium",
            threat_severity="critical" if level == "bad" else "minor",
            threat_label=f"Сближение с {c.get('sat_2_name', '?')}",
            limitations=env.get("limitations"),
        ))

    return impacts


def meteors_to_impacts(env: Optional[Dict[str, Any]]) -> List[Impact]:
    """Метеороидные потоки → интервалы повышенной пылевой нагрузки."""
    if not env or env.get("error") or not env.get("items"):
        return []

    thr = THRESHOLDS["meteor"]
    impacts: List[Impact] = []

    for m in env["items"]:
        zhr = m.get("zhr")
        if zhr is None:
            continue
        level = _classify(float(zhr), thr)
        if level == "good":
            continue

        try:
            start = _parse_iso(m["start"])
            end = _parse_iso(m["end"])
        except (KeyError, ValueError):
            continue

        impacts.append(Impact(
            start=start,
            end=end,
            level=level,
            kind="forecast",
            mechanism="meteor",
            source=env.get("source", "NASA MEO"),
            source_url=env.get("source_url"),
            publication_time=_parse_iso(env["fetched_at"]) if env.get("fetched_at") else None,
            value=float(zhr),
            unit=thr["unit"],
            confidence="medium",
            threat_severity="minor",
            threat_label=f"Поток {m.get('name', '?')}",
            limitations=env.get("limitations"),
        ))

    return impacts


# =========================================================
# Агрегатор
# =========================================================

def build_impacts(data: Dict[str, Any],
                  start: datetime,
                  end: datetime) -> List[Impact]:
    """Собирает все Impact из результата sources.collect_for_window.

    Пока подключены три механизма. Добавление нового — одна строка.
    """
    impacts: List[Impact] = []
    impacts += sep_to_impacts(data.get("sep"))
    impacts += cdm_to_impacts(data.get("conjunctions"))
    impacts += meteors_to_impacts(data.get("meteors"))
    # TODO: сюда добавляются новые конвертеры, когда аналитик их пришлёт:
    # impacts += sa_anomaly_to_impacts(data.get("saa"))
    # impacts += eclipse_to_impacts(data.get("eclipse"))

    return [i for i in impacts if _overlap(i, start, end)]


# =========================================================
# Рекомендация
# =========================================================

def pick_recommendation(windows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Выбирает окно с минимальной суммой риска.

    Веса — тоже эвристика. Аналитик может поменять.
    """
    if not windows:
        return None

    WEIGHTS = {"bad": 3.0, "warn": 1.0, "eclipse": 0.2}

    scored = []
    for w in windows:
        bad = w.get("minutes_bad", 0) or 0
        warn = w.get("minutes_warn", 0) or 0
        eclipse = (w.get("eclipse") or {}).get("shadow_minutes", 0) or 0
        score = bad * WEIGHTS["bad"] + warn * WEIGHTS["warn"] + eclipse * WEIGHTS["eclipse"]
        scored.append((score, w))

    scored.sort(key=lambda x: x[0])
    best_score, best = scored[0]

    return {
        "window_id": best.get("id"),
        "start": best.get("start"),
        "end": best.get("end"),
        "score": round(best_score, 2),
        "reason": (
            f"{best.get('minutes_bad', 0)} мин под bad, "
            f"{best.get('minutes_warn', 0)} мин под warn"
        ),
    }