"""
sources.py — слой доступа к внешним данным для анализа рисков ВКД.

Возвращает единый envelope:
    {
        "items": [...],
        "source": str,
        "source_url": str,
        "fetched_at": iso_z,
        "publication_time": iso_z | None,
        "version": str | None,
        "units": str | None,
        "limitations": str | None,
        "error": str | None,
    }

Пустой items + error=None  →  данных нет за период (не означает «безопасно»).
Пустой items + error!=None →  источник недоступен.

Режимы источников:
    current     — live-данные (SWPC, SOCRATES, CelesTrak GP, Kp forecast)
    historical  — архив с фильтром по времени публикации (cutoff)
    both        — работает в обоих режимах (DONKI, GFZ, GOES SGPS, Space-Track history)

Не знает про Impact, good/warn/bad и пороги — это ответственность metrics.py.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin

from requests_cache import CachedSession

# ---------------------------------------------------------------------------
# Опциональные зависимости
# ---------------------------------------------------------------------------
try:
    from spacetrack import SpaceTrackClient
    import spacetrack.operators as op
    _HAS_SPACETRACK = True
except ImportError:
    _HAS_SPACETRACK = False

try:
    import netCDF4 as nc
    import cftime
    _HAS_NETCDF = True
except ImportError:
    _HAS_NETCDF = False


# ---------------------------------------------------------------------------
# Конфигурация кэша
# ---------------------------------------------------------------------------

CACHE_DIR = Path(os.getenv("CACHE_DIR", "cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_URLS_EXPIRE: Dict[str, int] = {
    "celestrak.org/NORAD/elements/": 3600,
    "space-track.org/basicspacedata/query/class/gp_history": 86400,
    "space-track.org/basicspacedata/query/class/cdm_public": 1800,
    "data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/": 86400,
    "services.swpc.noaa.gov/": 300,
    "api.nasa.gov/DONKI/": 600,
    "kp.gfz.de/app/json/": 1800,
    "celestrak.org/SOCRATES": 3600,
}

session = CachedSession(
    cache_name=str(CACHE_DIR / "http_cache"),
    backend="sqlite",
    expire_after=600,
    urls_expire_after=_URLS_EXPIRE,
    allowable_methods=("GET", "POST"),
    stale_if_error=True,
)

_TTL_MAP: List[Tuple[str, int]] = list(_URLS_EXPIRE.items())


def configure(cache_dir: Optional[str] = None) -> None:
    """Переопределить директорию кэша (вызывается из app.py)."""
    global CACHE_DIR, session
    if cache_dir:
        CACHE_DIR = Path(cache_dir)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        session.cache.clear()
        session = CachedSession(
            cache_name=str(CACHE_DIR / "http_cache"),
            backend="sqlite",
            expire_after=600,
            urls_expire_after=_URLS_EXPIRE,
            allowable_methods=("GET", "POST"),
            stale_if_error=True,
        )


def set_ttl(pattern: str, seconds: int) -> None:
    """Программно изменить TTL для паттерна."""
    global _TTL_MAP
    _TTL_MAP = [(p, t) for p, t in _TTL_MAP if p != pattern]
    _TTL_MAP.insert(0, (pattern, seconds))
    session.settings.urls_expire_after = dict(_TTL_MAP)


def invalidate_cache() -> None:
    """Полный сброс HTTP-кэша."""
    session.cache.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(x: datetime) -> str:
    return x.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _gfz_fmt(x: datetime) -> str:
    """GFZ принимает только YYYY-MM-DDThh:mm:ssZ (без микросекунд)."""
    return x.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _envelope(
    *,
    items: List[Dict[str, Any]],
    source: str,
    source_url: Optional[str],
    version: Optional[str] = None,
    units: Optional[str] = None,
    publication_time: Optional[datetime] = None,
    limitations: Optional[str] = None,
    error: Optional[str] = None,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "items": items,
        "source": source,
        "source_url": source_url,
        "fetched_at": _iso_z(_now()),
        "publication_time": _iso_z(publication_time) if publication_time else None,
        "version": version,
        "units": units,
        "limitations": limitations,
        "error": error,
        "mode": mode,
    }


def _dedup(items: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for x in sorted(items, key=lambda i: i.get(key) or ""):
        k = x.get(key)
        if k and k in seen:
            continue
        if k:
            seen.add(k)
        out.append(x)
    return out


def _tle_epoch(line1: str) -> str:
    yy = int(line1[18:20])
    doy = float(line1[20:32])
    year = 2000 + yy if yy < 57 else 1900 + yy
    base = datetime(year, 1, 1, tzinfo=timezone.utc)
    return _iso_z(base + timedelta(days=doy - 1))


def _st_creds() -> Tuple[str, str]:
    identity = os.getenv("SPACETRACK_USER") or os.getenv("SPACETRACK_IDENTITY")
    password = os.getenv("SPACETRACK_PASSWORD")
    if not (identity and password):
        raise RuntimeError("Space-Track: задайте SPACETRACK_USER и SPACETRACK_PASSWORD")
    return identity, password


def _to_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ===========================================================================
# 1. CELESTRAK — текущий TLE МКС (current only)
# ===========================================================================

CELESTRAK_TLE_ISS = (
    "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE"
)


def fetch_tle_iss() -> Dict[str, Any]:
    """Текущий TLE МКС из CelesTrak. Только current.

    В historical использовать fetch_tle_history.
    """
    try:
        r = session.get(CELESTRAK_TLE_ISS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        return _envelope(
            items=[], source="Celestrak GP", source_url=CELESTRAK_TLE_ISS,
            error=f"{type(e).__name__}: {e}", mode="current",
            limitations="Без TLE расчёт положения МКС невозможен.",
        )

    lines = [ln.strip() for ln in r.text.splitlines() if ln.strip()]
    if len(lines) < 3:
        return _envelope(
            items=[], source="Celestrak GP", source_url=CELESTRAK_TLE_ISS,
            error=f"неполный ответ: {lines!r}", mode="current",
        )

    name, l1, l2 = lines[0], lines[1], lines[2]
    return _envelope(
        items=[{"name": name, "line1": l1, "line2": l2,
                "epoch_utc": _tle_epoch(l1)}],
        source="Celestrak GP", source_url=CELESTRAK_TLE_ISS,
        version="gp.php", units="TLE", mode="current",
        limitations="TLE ISS обновляется ~раз в сутки; давность = now - epoch.",
    )


# ===========================================================================
# 2. SPACE-TRACK — исторический TLE (gp_history, historical only)
# ===========================================================================

def fetch_tle_history(
    start: datetime,
    end: datetime,
    *,
    cutoff: Optional[datetime] = None,
    norad_id: int = 25544,
) -> Dict[str, Any]:
    """gp_history: все публикации TLE за [start, end].

    cutoff — момент запроса пользователя; более поздние публикации
    отбрасываются (T4, replay). Фильтр по CREATION_DATE, а не по EPOCH.
    """
    if not _HAS_SPACETRACK:
        return _envelope(
            items=[], source="Space-Track gp_history",
            source_url="https://www.space-track.org/basicspacedata/query/class/gp_history",
            error="spacetrack не установлен: pip install spacetrack",
            mode="historical",
        )

    try:
        identity, password = _st_creds()
        drange = op.inclusive_range(start - timedelta(days=7), end)
        items: List[Dict[str, Any]] = []
        with SpaceTrackClient(identity=identity, password=password) as st:
            raw = st.gp_history(
                norad_cat_id=norad_id,
                creation_date=drange,
                orderby="CREATION_DATE",
                format="json",
            )
        data = json.loads(raw) if isinstance(raw, str) else raw
        for x in data:
            items.append({
                "name": x.get("OBJECT_NAME"),
                "line1": x.get("TLE_LINE1"),
                "line2": x.get("TLE_LINE2"),
                "epoch_utc": x.get("EPOCH"),
                "creation_date": x.get("CREATION_DATE"),
            })
    except Exception as e:
        return _envelope(
            items=[], source="Space-Track gp_history",
            source_url="https://www.space-track.org/basicspacedata/query/class/gp_history",
            error=f"{type(e).__name__}: {e}", mode="historical",
        )

    if cutoff:
        items = [i for i in items
                 if i.get("creation_date") and _parse_iso(i["creation_date"]) <= cutoff]

    items = _dedup(items, "creation_date")
    return _envelope(
        items=items, source="Space-Track gp_history",
        source_url="https://www.space-track.org/basicspacedata/query/class/gp_history",
        version="gp_history", units="TLE", mode="historical",
        publication_time=cutoff,
        limitations=(
            "Исторические TLE отдаются с задержкой публикации; "
            "для строгого replay фильтруем по CREATION_DATE."
        ),
    )


# ===========================================================================
# 3. SPACE-TRACK — сближения (cdm_public, historical)
# ===========================================================================

def fetch_conjunctions(
    start: datetime,
    end: datetime,
    *,
    cutoff: Optional[datetime] = None,
    sat_id: int = 25544,
) -> Dict[str, Any]:
    """cdm_public: CDM для МКС (МКС может быть SAT_1 или SAT_2).

    cutoff фильтрует по CREATED, а не по TCA (T4, replay).
    """
    if not _HAS_SPACETRACK:
        return _envelope(
            items=[], source="Space-Track cdm_public",
            source_url="https://www.space-track.org/basicspacedata/query/class/cdm_public",
            error="spacetrack не установлен",
            limitations="Без Space-Track сближения не проверяются.",
            mode="historical",
        )

    try:
        identity, password = _st_creds()
        tca_range = op.inclusive_range(start - timedelta(days=1), end + timedelta(days=1))
        all_items: List[Dict[str, Any]] = []
        with SpaceTrackClient(identity=identity, password=password) as st:
            for field in ("sat_1_id", "sat_2_id"):
                raw = st.generic_request(
                    "cdm_public",
                    controller="basicspacedata",
                    **{field: sat_id},
                    tca=tca_range,
                    orderby="TCA asc",
                    format="json",
                )
                data = json.loads(raw) if isinstance(raw, str) else raw
                all_items.extend(_normalize_cdm(x) for x in data if x.get("TCA"))
    except Exception as e:
        return _envelope(
            items=[], source="Space-Track cdm_public",
            source_url="https://www.space-track.org/basicspacedata/query/class/cdm_public",
            error=f"{type(e).__name__}: {e}", mode="historical",
        )

    if cutoff:
        all_items = [i for i in all_items
                     if i.get("created") and _parse_iso(i["created"]) <= cutoff]

    seen = set()
    uniq: List[Dict[str, Any]] = []
    for i in all_items:
        k = (i.get("sat_2_id"), i.get("tca"))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(i)

    return _envelope(
        items=uniq, source="Space-Track cdm_public",
        source_url="https://www.space-track.org/basicspacedata/query/class/cdm_public",
        version="cdm_public", units="probability / km", mode="historical",
        publication_time=cutoff,
        limitations=(
            "CDM публикуются с задержкой; поздние уточнения не учитываются "
            "в историческом режиме (cutoff по CREATED). Для МКС cdm_public "
            "часто пуст: публичные CDM по пилотируемым объектам не публикуются. "
            "Отсутствие записей не означает отсутствие сближений."
        ),
    )


def _normalize_cdm(x: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "cdm_id": x.get("CDM_ID"),
        "created": x.get("CREATED"),
        "tca": x.get("TCA"),
        "min_range_km": _to_float(x.get("MIN_RNG")),
        "probability": _to_float(x.get("PC")),
        "miss_distance_km": _to_float(x.get("MISS_DISTANCE")),
        "sat_1_id": x.get("SAT_1_ID"),
        "sat_1_name": x.get("SAT_1_NAME"),
        "sat_2_id": x.get("SAT_2_ID"),
        "sat_2_name": x.get("SAT_2_NAME"),
        "emergency": x.get("EMERGENCY_REPORTABLE"),
    }


# ===========================================================================
# 4. CELESTRAK — SOCRATES Plus (current only, резерв для сближений)
# ===========================================================================

SOCRATES_URL = (
    "https://celestrak.org/SOCRATES/table-socrates.php"
    "?CATNR=25544&ORDER=MINRANGE&MAX=25"
)


class _SocratesParser:
    """Минимальный HTML-парсер таблицы SOCRATES."""
    def __init__(self):
        from html.parser import HTMLParser
        self.rows: List[List[str]] = []
        self._row: List[str] = []
        self._cell: List[str] = []
        self._in_cell = False
        self._parser = _SocratesHTML(self)

    def feed(self, html: str) -> None:
        self._parser.feed(html)


class _SocratesHTML:
    def __init__(self, parent):
        from html.parser import HTMLParser
        self.parent = parent
        outer = self

        class _Inner(HTMLParser):
            def handle_starttag(self, tag, attrs):
                if tag == "tr":
                    outer.parent._row = []
                elif tag in ("td", "th"):
                    outer.parent._in_cell = True
                    outer.parent._cell = []

            def handle_endtag(self, tag):
                if tag in ("td", "th"):
                    outer.parent._in_cell = False
                    outer.parent._row.append("".join(outer.parent._cell).strip())
                elif tag == "tr":
                    if outer.parent._row:
                        outer.parent.rows.append(outer.parent._row)

            def handle_data(self, data):
                if outer.parent._in_cell:
                    outer.parent._cell.append(data)

        self._inner = _Inner()

    def feed(self, html: str) -> None:
        self._inner.feed(html)


def _parse_socrates_html(html: str) -> List[Dict[str, Any]]:
    p = _SocratesParser()
    p.feed(html)
    if not p.rows:
        return []
    header = [h.lower().replace(" ", "_") for h in p.rows[0]]
    items: List[Dict[str, Any]] = []
    for row in p.rows[1:]:
        d = dict(zip(header, row))
        items.append({
            "sat_2_id": d.get("norad_cat_id") or d.get("norad") or d.get("catalog_number"),
            "sat_2_name": d.get("name") or d.get("object_name"),
            "tca": d.get("tca") or d.get("time_of_closest_approach"),
            "min_range_km": _to_float(d.get("min_range") or d.get("minrange") or d.get("minimum_range")),
            "probability": _to_float(d.get("max_prob") or d.get("maxprobability") or d.get("max_probability")),
        })
    return items


def fetch_socrates() -> Dict[str, Any]:
    """SOCRATES Plus: HTML-таблица. Только current, архива нет."""
    try:
        r = session.get(SOCRATES_URL, timeout=20)
        r.raise_for_status()
    except Exception as e:
        return _envelope(
            items=[], source="Celestrak SOCRATES",
            source_url=SOCRATES_URL,
            error=f"{type(e).__name__}: {e}",
            mode="current",
            limitations="Только текущие сближения; архива нет (CONJ-2).",
        )

    m = re.search(r"Data current as of\s*([^<]+)", r.text, re.IGNORECASE)
    data_current = m.group(1).strip() if m else None

    try:
        items = _parse_socrates_html(r.text)
    except Exception as e:
        return _envelope(
            items=[], source="Celestrak SOCRATES",
            source_url=SOCRATES_URL,
            error=f"parse: {type(e).__name__}: {e}",
            mode="current",
            limitations="Только текущие сближения; архива нет.",
        )

    return _envelope(
        items=items, source="Celestrak SOCRATES",
        source_url=SOCRATES_URL,
        version=f"SOCRATES Plus (data current as of {data_current})" if data_current else "SOCRATES Plus",
        units="km / Pc", mode="current",
        limitations=(
            "SOCRATES даёт верхнюю оценку Pc; не смешивать с Pc из CDM. "
            "Только текущее, архива нет."
        ),
    )


# ===========================================================================
# 5. NOAA SWPC — оперативные данные (current only)
# ===========================================================================

SWPC_BASE = "https://services.swpc.noaa.gov"


def _rows_from_swpc_json(raw) -> List[Dict[str, Any]]:
    """SWPC отдаёт либо [header, ...rows], либо [{...}, ...]."""
    if not raw:
        return []
    if isinstance(raw[0], dict):
        return list(raw)
    header, *rows = raw
    return [dict(zip(header, row)) for row in rows]


def _swpc_live_only(source: str, url: str) -> Dict[str, Any]:
    """Заглушка для historical-режима: SWPC не имеет архива."""
    return _envelope(
        items=[], source=source, source_url=url,
        error="SWPC не хранит архив; источник только для current режима",
        mode="current",
        limitations="Для replay используйте NCEI/GFZ/DONKI.",
    )


def fetch_swpc_protons() -> Dict[str, Any]:
    """Интегральные протоны GOES (1-day). Канал ≥10 МэВ. Current only."""
    url = f"{SWPC_BASE}/json/goes/primary/integral-protons-1-day.json"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        return _envelope(
            items=[], source="NOAA SWPC protons",
            source_url=url, error=f"{type(e).__name__}: {e}",
            mode="current",
        )

    items: List[Dict[str, Any]] = []
    for row in _rows_from_swpc_json(raw):
        energy = (row.get("energy") or "").replace(" ", "")
        if "10" not in energy:
            continue
        try:
            items.append({
                "time_utc": _iso_z(_parse_iso(row["time_tag"])),
                "flux_pfu": float(row["flux"]),
                "channel": "P7",
                "kind": "diff",
            })
        except (KeyError, TypeError, ValueError):
            continue

    return _envelope(
        items=items, source="NOAA SWPC protons",
        source_url=url, version="integral-protons-1-day",
        units="pfu", mode="current",
        limitations="SWPC отдаёт последние сутки; для replay — NCEI SGPS (SEP-1).",
    )


def fetch_swpc_kp() -> Dict[str, Any]:
    """Планетарный Kp (оценка по 8 из 13 станций). Current only."""
    url = f"{SWPC_BASE}/products/noaa-planetary-k-index.json"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        return _envelope(
            items=[], source="NOAA SWPC Kp",
            source_url=url, error=f"{type(e).__name__}: {e}",
            mode="current",
        )

    items: List[Dict[str, Any]] = []
    for d in _rows_from_swpc_json(raw):
        try:
            items.append({
                "time_utc": _iso_z(_parse_iso(d["time_tag"])),
                "kp": float(d["Kp"]),
                "a_running": float(d.get("a_running") or 0),
                "station_count": int(d.get("station_count") or 0),
            })
        except (KeyError, TypeError, ValueError):
            continue

    return _envelope(
        items=items, source="NOAA SWPC Kp",
        source_url=url, version="planetary-k-index",
        units="Kp", mode="current",
        limitations=(
            "Оценка по 8 из 13 станций; station_count — индикатор уверенности. "
            "Только последние ~7 суток; архива нет."
        ),
    )


def fetch_swpc_kp_forecast() -> Dict[str, Any]:
    """Прогноз Kp по 3-часовым интервалам. Current only."""
    url = f"{SWPC_BASE}/products/noaa-planetary-k-index-forecast.json"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        return _envelope(
            items=[], source="NOAA SWPC Kp forecast",
            source_url=url, error=f"{type(e).__name__}: {e}",
            mode="current",
        )

    items: List[Dict[str, Any]] = []
    for d in _rows_from_swpc_json(raw):
        try:
            items.append({
                "time_utc": _iso_z(_parse_iso(d["time_tag"])),
                "kp": float(d["kp"]),
                "observed": (d.get("observed") or "").lower() == "observed",
                "noaa_scale": d.get("noaa_scale"),
            })
        except (KeyError, TypeError, ValueError):
            continue

    return _envelope(
        items=items, source="NOAA SWPC Kp forecast",
        source_url=url, version="planetary-k-index-forecast",
        units="Kp", mode="current",
        limitations=(
            "Смесь observed (прошлое) и forecast (будущее); "
            "фильтруйте по полю 'observed'. Архива нет."
        ),
    )


def fetch_swpc_alerts() -> Dict[str, Any]:
    """Активные alerts/watches/warnings SWPC. Current only."""
    url = f"{SWPC_BASE}/products/alerts.json"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        return _envelope(
            items=[], source="NOAA SWPC alerts",
            source_url=url, error=f"{type(e).__name__}: {e}",
            mode="current",
        )

    items: List[Dict[str, Any]] = []
    for a in raw:
        items.append({
            "issued": a.get("issue_datetime"),
            "message": a.get("message", ""),
            "product_id": a.get("product_id"),
            "valid_from": a.get("valid_from"),
            "valid_to": a.get("valid_to"),
        })

    return _envelope(
        items=items, source="NOAA SWPC alerts",
        source_url=url, version="alerts", units="—", mode="current",
        limitations=(
            "Три времени (issued, valid_from, valid_to) хранить раздельно. "
            "Архива нет; для replay — NCEI RSGA (SEP-5)."
        ),
    )


def fetch_swpc_3day_forecast() -> Dict[str, Any]:
    """3-day forecast (текст). Current only."""
    url = f"{SWPC_BASE}/text/3-day-forecast.txt"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
    except Exception as e:
        return _envelope(
            items=[], source="NOAA SWPC 3-day forecast",
            source_url=url, error=f"{type(e).__name__}: {e}",
            mode="current",
        )

    text = r.text
    issued = None
    m = re.search(r"Issued:\s*(.+)", text)
    if m:
        issued = m.group(1).strip()

    return _envelope(
        items=[{"text": text, "issued_text": issued}],
        source="NOAA SWPC 3-day forecast",
        source_url=url, version="3-day-forecast",
        units="—", mode="current",
        limitations=(
            "Текстовый формат; разбор вероятностей требует тестов (T7). "
            "Архива нет."
        ),
    )


# ===========================================================================
# 6. GFZ POTSDAM — Kp (both: nowcast для current, def для historical)
# ===========================================================================

GFZ_KP_URL = "https://kp.gfz.de/app/json/"


def fetch_gfz_kp(
    start: datetime,
    end: datetime,
    *,
    cutoff: Optional[datetime] = None,
    lookback_days: int = 3,
) -> Dict[str, Any]:
    """Kp из GFZ (3-часовой, без прогноза вперёд).

    status='def' — окончательные значения, доступны с задержкой >45 дней.
    status='all' — nowcast для свежих дат.
    """
    now = _now()
    is_historical = cutoff is not None
    if is_historical and (now - cutoff).days > 45:
        status = "def"
        version = "Kp_definitive"
        mode = "historical"
        limitations = (
            "Архивное значение (def): окончательные данные, не то, "
            "что было известно оператору. Для оперативных — SWPC Kp (G-2)."
        )
    else:
        status = "all"
        version = "Kp_all"
        mode = "current"
        limitations = (
            "Смесь nowcast/definitive. GFZ не даёт прогноз вперёд; "
            "для будущих окон используйте SWPC Kp forecast."
        )

    query_start = min(start, end) - timedelta(days=lookback_days)
    query_end = max(start, end)

    params = {
        "start": _gfz_fmt(query_start),
        "end": _gfz_fmt(query_end),
        "index": "Kp",
        "status": status,
    }
    try:
        r = session.get(GFZ_KP_URL, params=params, timeout=20)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        return _envelope(
            items=[], source="GFZ Potsdam Kp",
            source_url=GFZ_KP_URL,
            error=f"{type(e).__name__}: {e}",
            limitations=limitations, mode=mode,
        )

    items: List[Dict[str, Any]] = []
    datetimes = raw.get("datetime") or []
    kps = raw.get("Kp") or []
    statuses = raw.get("status") or []
    for i, ts in enumerate(datetimes):
        try:
            items.append({
                "time_utc": _iso_z(_parse_iso(ts)),
                "kp": float(kps[i]),
                "status": statuses[i] if i < len(statuses) else status,
            })
        except (IndexError, TypeError, ValueError):
            continue

    return _envelope(
        items=items, source="GFZ Potsdam Kp",
        source_url=r.url,
        version=version, units="Kp", mode=mode,
        publication_time=cutoff,
        limitations=limitations,
    )


# ===========================================================================
# 7. NASA DONKI — архив событий (both)
# ===========================================================================

DONKI_BASE = "https://api.nasa.gov/DONKI"
DONKI_MAX_WINDOW_DAYS = 30


def _donki_api_key() -> str:
    return os.getenv("NASA_API_KEY", "DEMO_KEY")


def _donki_get(path: str, start: datetime, end: datetime,
               extra: Optional[Dict[str, str]] = None) -> Any:
    params = {
        "startDate": start.strftime("%Y-%m-%d"),
        "endDate": end.strftime("%Y-%m-%d"),
        "api_key": _donki_api_key(),
    }
    if extra:
        params.update(extra)
    url = f"{DONKI_BASE}{path}"
    r = session.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _donki_windows(start: datetime, end: datetime) -> Iterable[Tuple[datetime, datetime]]:
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=DONKI_MAX_WINDOW_DAYS), end)
        yield cur, nxt
        cur = nxt


def _donki_filter_cutoff(items, cutoff, field):
    if cutoff:
        items = [i for i in items
                 if i.get(field) and _parse_iso(i[field]) <= cutoff]
    return items


def fetch_donki_sep(start, end, *, cutoff=None) -> Dict[str, Any]:
    """DONKI SEP — каталог событий (не прогноз)."""
    items: List[Dict[str, Any]] = []
    try:
        for s, e in _donki_windows(start, end):
            data = _donki_get("/SEP", s, e)
            for x in data or []:
                items.append({
                    "sep_id": x.get("sepID"),
                    "event_time": x.get("eventTime"),
                    "submission_time": x.get("submissionTime"),
                    "instruments": x.get("instruments"),
                    "linked_events": x.get("linkedEvents"),
                })
    except Exception as e:
        return _envelope(
            items=[], source="NASA DONKI SEP",
            source_url=f"{DONKI_BASE}/SEP",
            error=f"{type(e).__name__}: {e}",
            mode="historical" if cutoff else "current",
        )

    items = _donki_filter_cutoff(items, cutoff, "submission_time")
    items = _dedup(items, "sep_id")
    return _envelope(
        items=items, source="NASA DONKI SEP",
        source_url=f"{DONKI_BASE}/SEP",
        version="DONKI SEP", units="—",
        mode="historical" if cutoff else "current",
        publication_time=cutoff,
        limitations="Каталог событий. Replay — по submissionTime.",
    )


def fetch_donki_gst(start, end, *, cutoff=None) -> Dict[str, Any]:
    """DONKI GST — геомагнитные бури."""
    items: List[Dict[str, Any]] = []
    try:
        for s, e in _donki_windows(start, end):
            data = _donki_get("/GST", s, e)
            for x in data or []:
                items.append({
                    "gst_id": x.get("gstID"),
                    "start_time": x.get("startTime"),
                    "submission_time": x.get("submissionTime"),
                    "all_kp": x.get("allKpIndex"),
                    "linked_events": x.get("linkedEvents"),
                })
    except Exception as e:
        return _envelope(
            items=[], source="NASA DONKI GST",
            source_url=f"{DONKI_BASE}/GST",
            error=f"{type(e).__name__}: {e}",
            mode="historical" if cutoff else "current",
        )

    items = _donki_filter_cutoff(items, cutoff, "submission_time")
    items = _dedup(items, "gst_id")
    return _envelope(
        items=items, source="NASA DONKI GST",
        source_url=f"{DONKI_BASE}/GST",
        version="DONKI GST", units="Kp",
        mode="historical" if cutoff else "current",
        publication_time=cutoff,
        limitations="Replay по submissionTime.",
    )


def fetch_donki_cmeanalysis(start, end, *, cutoff=None) -> Dict[str, Any]:
    """DONKI CMEAnalysis — WSA-ENLIL расчёт прихода CME (±7 ч)."""
    items: List[Dict[str, Any]] = []
    try:
        for s, e in _donki_windows(start, end):
            data = _donki_get("/CMEAnalysis", s, e)
            for x in data or []:
                items.append({
                    "analysis_id": x.get("analysisID"),
                    "associated_cme": x.get("associatedCMEID"),
                    "time_21_5": x.get("time21_5"),
                    "submission_time": x.get("submissionTime"),
                    "estimated_shock_arrival_time": x.get("estimatedShockArrivalTime"),
                    "estimated_duration": x.get("estimatedDuration"),
                    "is_most_accurate": x.get("isMostAccurate"),
                    "link": x.get("link"),
                })
    except Exception as e:
        return _envelope(
            items=[], source="NASA DONKI CMEAnalysis",
            source_url=f"{DONKI_BASE}/CMEAnalysis",
            error=f"{type(e).__name__}: {e}",
            mode="historical" if cutoff else "current",
        )

    items = _donki_filter_cutoff(items, cutoff, "submission_time")
    items = _dedup(items, "analysis_id")
    return _envelope(
        items=items, source="NASA DONKI CMEAnalysis",
        source_url=f"{DONKI_BASE}/CMEAnalysis",
        version="DONKI CMEAnalysis", units="—",
        mode="historical" if cutoff else "current",
        publication_time=cutoff,
        limitations=(
            "Интервал прихода CME передавать как интервал, а не точку. "
            "Replay — по submissionTime."
        ),
    )


def fetch_donki_notifications(start, end, *, cutoff=None) -> Dict[str, Any]:
    """DONKI notifications — сообщения с временем выпуска."""
    items: List[Dict[str, Any]] = []
    try:
        for s, e in _donki_windows(start, end):
            data = _donki_get("/notifications", s, e, {"type": "all"})
            for x in data or []:
                items.append({
                    "message_id": x.get("messageID"),
                    "message_type": x.get("messageType"),
                    "message_issue_time": x.get("messageIssueTime"),
                    "message_url": x.get("messageURL"),
                    "message_body": (x.get("messageBody") or "")[:2000],
                })
    except Exception as e:
        return _envelope(
            items=[], source="NASA DONKI notifications",
            source_url=f"{DONKI_BASE}/notifications",
            error=f"{type(e).__name__}: {e}",
            mode="historical" if cutoff else "current",
        )

    items = _donki_filter_cutoff(items, cutoff, "message_issue_time")
    items = _dedup(items, "message_id")
    return _envelope(
        items=items, source="NASA DONKI notifications",
        source_url=f"{DONKI_BASE}/notifications",
        version="DONKI notifications", units="—",
        mode="historical" if cutoff else "current",
        publication_time=cutoff,
        limitations="Максимум 30 дней за запрос. Replay — по messageIssueTime.",
    )


# ===========================================================================
# 8. NOAA NCEI — GOES SGPS L2 avg5m (both, архив отстаёт ~7 суток)
# ===========================================================================

GOES_BASE = (
    "https://data.ngdc.noaa.gov/platforms/solar-space-observing-"
    "satellites/goes/goes{sat}/l2/data/sgps-l2-avg5m/{y}/{m:02d}/"
)

_GOES_FILE_RE = re.compile(r'sci_sgps-l2-avg5m_g\d+_d(\d{8})_(v[\d-]+)\.nc')

_SEP_FALLBACK_CHANNELS = [
    "P1", "P2A", "P2B", "P3", "P4", "P5",
    "P6", "P7", "P8A", "P8B", "P8C", "P9", "P10",
]


def _resolve_goes_file(day: datetime, sat: str) -> Optional[str]:
    dir_url = GOES_BASE.format(sat=sat, y=day.year, m=day.month)
    try:
        r = session.get(dir_url, timeout=20)
        r.raise_for_status()
    except Exception:
        return None

    best: Optional[Tuple[str, str]] = None
    for m in _GOES_FILE_RE.finditer(r.text):
        if m.group(1) == day.strftime("%Y%m%d"):
            ver = m.group(2)
            if best is None or ver > best[1]:
                best = (m.group(0), ver)
    if not best:
        return None
    return urljoin(dir_url, best[0])


def fetch_sep_proton_flux(
    start: datetime,
    end: datetime,
    *,
    cutoff: Optional[datetime] = None,
    sat: Optional[str] = None,
    channels: Iterable[str] = ("P7",),
    include_integral: bool = True,
) -> Dict[str, Any]:
    """Читает NetCDF GOES SGPS L2 avg5m.

    sat: '16' для 2024-05 (East), '19' для current. Если None — env GOES_SAT.
    """
    if not _HAS_NETCDF:
        return _envelope(
            items=[], source="NOAA GOES SEP",
            source_url="https://data.ngdc.noaa.gov/",
            error="netCDF4 не установлен: pip install netCDF4 cftime",
        )

    sat = sat or os.getenv("GOES_SAT", "16")

    points: List[Dict[str, Any]] = []
    urls: List[str] = []
    errors: List[str] = []

    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= end:
        url = _resolve_goes_file(day, sat)
        if not url:
            day += timedelta(days=1)
            continue

        try:
            r = session.get(url, timeout=60)
            r.raise_for_status()
        except Exception as e:
            errors.append(f"{day.date()}: {type(e).__name__}: {e}")
            day += timedelta(days=1)
            continue

        urls.append(url)
        try:
            ds = nc.Dataset("inmemory.nc", memory=r.content)
            try:
                times = _read_goes_time(ds)

                if "AvgDiffProtonFlux" in ds.variables:
                    diff = ds["AvgDiffProtonFlux"]
                    channel_names = _resolve_channel_names(diff)
                    for ch in channels:
                        idx = _resolve_channel_index(ch, channel_names)
                        if idx is None:
                            continue
                        series = diff[:][:, 0, idx]
                        _append_series(points, times, series, start, end, cutoff,
                                       kind="diff", channel=ch)

                if include_integral and "AvgIntProtonFlux" in ds.variables:
                    integral = ds["AvgIntProtonFlux"]
                    series = integral[:][:, 0]
                    _append_series(points, times, series, start, end, cutoff,
                                   kind="integral", channel=">500MeV")
            finally:
                ds.close()
        except Exception as e:
            errors.append(f"{day.date()} parse: {type(e).__name__}: {e}")

        day += timedelta(days=1)

    deduped: Dict[tuple, Dict[str, Any]] = {}
    for p in points:
        deduped[(p["time_utc"], p["kind"], p["channel"])] = p
    items = sorted(deduped.values(), key=lambda p: (p["time_utc"], p["channel"]))

    error_msg = "; ".join(errors) if errors and not items else None
    mode = "historical" if cutoff else "current"

    return _envelope(
        items=items, source=f"NOAA GOES-{sat} SGPS",
        source_url=urls[0] if urls else GOES_BASE.format(sat=sat, y=start.year, m=start.month),
        version="sgps-l2-avg5m", units="pfu (protons/cm^2 s sr)",
        publication_time=cutoff, error=error_msg, mode=mode,
        limitations=(
            "SGPS L2 avg5m — 5-минутные средние. Версия файла берётся из листинга NCEI. "
            "Архив NCEI отстаёт ~7 суток; для свежего current используйте SWPC protons."
        ),
    )


def _append_series(points, times, series, start, end, cutoff, *,
                   kind: str, channel: str) -> None:
    if len(series) != len(times):
        return
    for t, v in zip(times, series):
        if t < start or t > end:
            continue
        if cutoff and t > cutoff:
            continue
        try:
            val = float(v)
        except (TypeError, ValueError):
            continue
        if val <= 0:
            continue
        points.append({
            "time_utc": _iso_z(t),
            "kind": kind,
            "channel": channel,
            "flux_pfu": val,
        })


def _resolve_channel_names(var) -> List[str]:
    for attr in ("diff_channels", "channel_names", "channels"):
        names = getattr(var, attr, None)
        if names is None:
            continue
        if isinstance(names, str):
            names = names.replace(",", " ").split()
        elif isinstance(names, bytes):
            names = names.decode("ascii", "ignore").replace(",", " ").split()
        else:
            try:
                names = [str(n).strip() for n in names]
            except TypeError:
                continue
        if names:
            return [n.upper() for n in names]
    return [c.upper() for c in _SEP_FALLBACK_CHANNELS]


def _resolve_channel_index(wanted: str, channel_names: List[str]) -> Optional[int]:
    wanted_upper = wanted.upper()
    for i, name in enumerate(channel_names):
        if name == wanted_upper:
            return i
    return None


def _read_goes_time(ds) -> List[datetime]:
    var = None
    for name in ("L2_SciData_TimeStamp", "time"):
        if name in ds.variables:
            var = ds[name]
            break
    if var is None:
        raise IndexError("No time variable found in NetCDF file")

    raw = var[:]
    units = getattr(var, "units", None)
    if units is None and "time" in ds.variables:
        units = getattr(ds["time"], "units", None)

    if units:
        try:
            py = cftime.num2pydate(raw, units)
            return [
                datetime(x.year, x.month, x.day, x.hour, x.minute,
                         int(x.second), tzinfo=timezone.utc)
                for x in py
            ]
        except Exception:
            pass

    out: List[datetime] = []
    for v in raw:
        try:
            out.append(datetime.fromtimestamp(float(v), tz=timezone.utc))
        except (TypeError, ValueError, OSError):
            continue
    return out


# ===========================================================================
# 9. Метеороидные потоки (both, локальный JSON)
# ===========================================================================

METEORS_LOCAL = Path(__file__).resolve().parent / "static" / "data" / "meteor_showers.json"


def fetch_meteor_showers() -> Dict[str, Any]:
    """Календарь метеороидных потоков из локального JSON."""
    try:
        raw = json.loads(METEORS_LOCAL.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else raw.get("showers") or raw.get("items") or []
        error = None
    except Exception as e:
        items = _METEORS_FALLBACK
        error = f"local file: {type(e).__name__}: {e}"

    return _envelope(
        items=items, source="Meteor showers (local)",
        source_url=str(METEORS_LOCAL),
        version="static-2026", units="ZHR",
        error=error, mode="both",
        limitations=(
            "Статистический календарь потоков. Не учитывает реальную плотность пыли; "
            "не каталог отдельных частиц."
        ),
    )


_METEORS_FALLBACK: List[Dict[str, Any]] = [
    {"name": "Eta Aquariids", "start": "2024-05-05T00:00:00Z",
     "end": "2024-05-07T00:00:00Z", "zhr": 50},
    {"name": "Perseids", "start": "2024-08-11T00:00:00Z",
     "end": "2024-08-13T00:00:00Z", "zhr": 100},
    {"name": "Orionids", "start": "2024-10-21T00:00:00Z",
     "end": "2024-10-22T00:00:00Z", "zhr": 20},
    {"name": "Leonids", "start": "2024-11-17T00:00:00Z",
     "end": "2024-11-18T00:00:00Z", "zhr": 15},
    {"name": "Geminids", "start": "2024-12-13T00:00:00Z",
     "end": "2024-12-15T00:00:00Z", "zhr": 150},
]


# ===========================================================================
# 10. Реестр источников и агрегатор
# ===========================================================================

def list_registered_sources() -> List[Dict[str, Any]]:
    """Перечень зарегистрированных источников для /api/sources.

    Поле modes: ['current'], ['historical'] или ['current', 'historical'].
    """
    return [
        # --- Орбита ---
        {"id": "tle", "factor": "orbit", "source": "Celestrak GP",
         "role": "operational TLE", "modes": ["current"], "ttl": 3600},
        {"id": "tle_history", "factor": "orbit", "source": "Space-Track gp_history",
         "role": "historical TLE", "modes": ["historical"], "ttl": 86400},

        # --- Сближения ---
        {"id": "conjunctions", "factor": "conjunction", "source": "Space-Track cdm_public",
         "role": "conjunctions (historical)", "modes": ["historical"], "ttl": 1800},
        {"id": "socrates", "factor": "conjunction", "source": "Celestrak SOCRATES",
         "role": "conjunctions (current, reserve)", "modes": ["current"], "ttl": 3600},

        # --- SEP ---
        {"id": "sep_goes_archive", "factor": "SEP", "source": "NOAA NCEI GOES",
         "role": "proton flux (archive)", "modes": ["current", "historical"], "ttl": 86400},
        {"id": "sep_swpc_protons", "factor": "SEP", "source": "NOAA SWPC",
         "role": "proton flux (live)", "modes": ["current"], "ttl": 300},

        # --- G (геомагнитные бури) ---
        {"id": "swpc_kp", "factor": "G", "source": "NOAA SWPC",
         "role": "Kp nowcast", "modes": ["current"], "ttl": 300},
        {"id": "swpc_kp_forecast", "factor": "G", "source": "NOAA SWPC",
         "role": "Kp forecast", "modes": ["current"], "ttl": 900},
        {"id": "gfz_kp", "factor": "G", "source": "GFZ Potsdam",
         "role": "Kp (all/def)", "modes": ["current", "historical"], "ttl": 1800},
        {"id": "donki_gst", "factor": "G", "source": "NASA DONKI",
         "role": "GST events", "modes": ["current", "historical"], "ttl": 600},
        {"id": "donki_cme", "factor": "G", "source": "NASA DONKI",
         "role": "CME arrival forecast", "modes": ["current", "historical"], "ttl": 600},

        # --- SEP/G события и алерты ---
        {"id": "swpc_alerts", "factor": "SEP/G", "source": "NOAA SWPC",
         "role": "alerts", "modes": ["current"], "ttl": 300},
        {"id": "swpc_3day", "factor": "SEP/G", "source": "NOAA SWPC",
         "role": "3-day forecast", "modes": ["current"], "ttl": 3600},
        {"id": "donki_sep", "factor": "SEP", "source": "NASA DONKI",
         "role": "SEP events", "modes": ["current", "historical"], "ttl": 600},
        {"id": "donki_notif", "factor": "SEP/G", "source": "NASA DONKI",
         "role": "notifications", "modes": ["current", "historical"], "ttl": 600},

        # --- Метеороиды ---
        {"id": "meteors", "factor": "meteor", "source": "local",
         "role": "shower calendar", "modes": ["current", "historical"], "ttl": 0},
    ]


def collect_for_window(
    start: datetime,
    end: datetime,
    *,
    cutoff: Optional[datetime] = None,
    include_historical_tle: bool = False,
    include_cdm: bool = True,
    include_sep: bool = True,
    include_meteors: bool = True,
    include_swpc: bool = True,
    include_donki: bool = True,
    include_gfz: bool = True,
    include_socrates: bool = False,
) -> Dict[str, Any]:
    """Собирает источники для окна [start, end].

    cutoff задан → исторический режим. В нём автоматически отключаются
    источники без архива (SWPC live, SOCRATES, CelesTrak GP), даже если
    флаги включены. Это защита T4 от подмешивания свежих данных.

    Никогда не бросает — ошибки складываются в result["errors"].
    """
    is_historical = cutoff is not None
    result: Dict[str, Any] = {
        "window": {"start": _iso_z(start), "end": _iso_z(end)},
        "mode": "historical" if is_historical else "current",
        "cutoff": _iso_z(cutoff) if cutoff else None,
        "errors": {},
        "skipped_live_only": [],
    }

    def _try(name: str, fn, *args, **kwargs) -> None:
        try:
            env = fn(*args, **kwargs)
        except Exception as e:
            env = _envelope(
                items=[], source=name, source_url=None,
                error=f"{type(e).__name__}: {e}",
                mode="historical" if is_historical else "current",
            )
        result[name] = env
        if env.get("error"):
            result["errors"][name] = env["error"]

    def _skip_live_only(name: str) -> None:
        result["skipped_live_only"].append(name)

    # --- Орбита ---
    if is_historical:
        if include_historical_tle:
            _try("tle_history", fetch_tle_history, start, end, cutoff=cutoff)
    else:
        _try("tle", fetch_tle_iss)

    # --- Сближения ---
    if include_cdm:
        if is_historical:
            _try("conjunctions", fetch_conjunctions, start, end, cutoff=cutoff)
        else:
            # В current cdm_public тоже работает, но TCA в будущем.
            _try("conjunctions", fetch_conjunctions, start, end, cutoff=None)
    if include_socrates:
        if is_historical:
            _skip_live_only("socrates")
        else:
            _try("socrates", fetch_socrates)

    # --- SEP ---
    if include_sep:
        # GOES SGPS: работает в обоих режимах (архив NCEI)
        _try("sep_goes_archive", fetch_sep_proton_flux, start, end, cutoff=cutoff)
        # SWPC protons: live only
        if include_swpc:
            if is_historical:
                _skip_live_only("sep_swpc_protons")
            else:
                _try("sep_swpc_protons", fetch_swpc_protons)

    # --- SWPC live-only: не вызываем в historical ---
    if include_swpc:
        for name, fn in (
            ("swpc_kp", fetch_swpc_kp),
            ("swpc_kp_forecast", fetch_swpc_kp_forecast),
            ("swpc_alerts", fetch_swpc_alerts),
            ("swpc_3day", fetch_swpc_3day_forecast),
        ):
            if is_historical:
                _skip_live_only(name)
            else:
                _try(name, fn)

    # --- GFZ Kp: работает в обоих режимах ---
    if include_gfz:
        _try("gfz_kp", fetch_gfz_kp, start, end, cutoff=cutoff)

    # --- DONKI: архив, работает в обоих режимах ---
    if include_donki:
        _try("donki_sep", fetch_donki_sep, start, end, cutoff=cutoff)
        _try("donki_gst", fetch_donki_gst, start, end, cutoff=cutoff)
        _try("donki_cme", fetch_donki_cmeanalysis, start, end, cutoff=cutoff)
        _try("donki_notif", fetch_donki_notifications, start, end, cutoff=cutoff)

    # --- Метеороиды: работает в обоих режимах ---
    if include_meteors:
        _try("meteors", fetch_meteor_showers)

    return result