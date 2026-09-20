"""
sources.py — слой доступа к внешним данным для анализа рисков ВКД.

Соответствует реестру источников Reestr_istochnikov_VKD.xlsx.
Расчётные компоненты (SGP4, IGRF-14, собственный расчёт сближений)
живут в analysis/orbit.py и здесь не реализованы.

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
        "mode": str | None,
    }

Режимы:
    current     — live (SWPC, SOCRATES, CelesTrak GP)
    historical  — архив с фильтром по времени публикации (cutoff)
    both        — работает в обоих режимах (DONKI, GFZ, GOES, RSGA, Space-Track history)
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
# Кэш
# ---------------------------------------------------------------------------

CACHE_DIR = Path(os.getenv("CACHE_DIR", "cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_URLS_EXPIRE: Dict[str, int] = {
    "celestrak.org/NORAD/elements/": 7200,
    "space-track.org/basicspacedata/query/class/gp_history": 86400,
    "space-track.org/basicspacedata/query/class/cdm_public": 1800,
    "data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/": 86400,
    "www.ngdc.noaa.gov/stp/space-weather/swpc-products/daily_reports/": 86400,
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
    global _TTL_MAP
    _TTL_MAP = [(p, t) for p, t in _TTL_MAP if p != pattern]
    _TTL_MAP.insert(0, (pattern, seconds))
    session.settings.urls_expire_after = dict(_TTL_MAP)


def invalidate_cache() -> None:
    session.cache.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(x: datetime) -> str:
    return x.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _gfz_fmt(x: datetime) -> str:
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
    identity = os.getenv("SPACETRACK_USER", "aefremova406@gmail.com")
    password = os.getenv("SPACETRACK_PASSWORD", "qodny6-joptuN-xigcyd")
    if not (identity and password):
        raise RuntimeError("Space-Track: задайте SPACETRACK_USER и SPACETRACK_PASSWORD")
    return identity, password


def _to_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ===========================================================================
# SAA-2. CelesTrak GP — текущий TLE МКС (current only)
# ===========================================================================

CELESTRAK_TLE_ISS = (
    "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE"
)


def fetch_tle_iss() -> Dict[str, Any]:
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
        items=[{"name": name, "line1": l1, "line2": l2, "epoch_utc": _tle_epoch(l1)}],
        source="Celestrak GP", source_url=CELESTRAK_TLE_ISS,
        version="gp.php", units="TLE", mode="current",
        limitations="TLE ISS обновляется ~раз в сутки; давность = now - epoch.",
    )


# ===========================================================================
# SAA-1. Space-Track gp_history — исторический TLE (historical)
# ===========================================================================

def fetch_tle_history(
    start: datetime,
    end: datetime,
    *,
    cutoff: Optional[datetime] = None,
    norad_id: int = 25544,
) -> Dict[str, Any]:
    """gp_history по EPOCH. Cutoff по CREATION_DATE (T4).

    Аналитик указывает запрос по EPOCH=2024-04-25..2024-07-01, чтобы
    для первых окон был предыдущий набор элементов. Replay — по
    CREATION_DATE <= cutoff.
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
        epoch_range = op.inclusive_range(start - timedelta(days=7), end)
        items: List[Dict[str, Any]] = []
        with SpaceTrackClient(identity=identity, password=password) as st:
            raw = st.gp_history(
                norad_cat_id=norad_id,
                epoch=epoch_range,        # SAA-1: запрос по EPOCH
                orderby="EPOCH",
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
            "Запрос по EPOCH, cutoff по CREATION_DATE. "
            "Пробелы больше суток между элементами — считать пропусками (T1)."
        ),
    )


# ===========================================================================
# CONJ-1 / CONJ-3. Space-Track cdm_public — сближения (historical)
# ===========================================================================

def fetch_conjunctions(
    start: datetime,
    end: datetime,
    *,
    cutoff: Optional[datetime] = None,
    sat_id: int = 25544,
) -> Dict[str, Any]:
    """cdm_public: МКС может быть SAT_1_ID или SAT_2_ID.

    Два запроса. Cutoff по CREATED, дедуп по (объект, TCA).
    CONJ-3 (будущие TCA) покрывается тем же вызовом.
    """
    if not _HAS_SPACETRACK:
        return _envelope(
            items=[], source="Space-Track cdm_public",
            source_url="https://www.space-track.org/basicspacedata/query/class/cdm_public",
            error="spacetrack не установлен", mode="historical",
            limitations="Без Space-Track сближения не проверяются.",
        )

    try:
        identity, password = _st_creds()
        tca_range = op.inclusive_range(start - timedelta(days=1), end + timedelta(days=1))
        all_items: List[Dict[str, Any]] = []
        with SpaceTrackClient(identity=identity, password=password) as st:
            for field in ("sat_1_id", "sat_2_id"):
                raw = st.generic_request(
                    "cdm_public", controller="basicspacedata",
                    **{field: sat_id},
                    tca=tca_range,
                    orderby="TCA asc", format="json",
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
            "Два запроса (SAT_1_ID и SAT_2_ID), дедуп по (объект, TCA). "
            "Cutoff по CREATED. Для МКС cdm_public часто пуст — публичные CDM "
            "по пилотируемым объектам не публикуются. Отсутствие записей "
            "не означает отсутствие сближений. CDM-сервис переносится в TraCSS."
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
# CONJ-2. CelesTrak SOCRATES Plus (current only)
# ===========================================================================

SOCRATES_URL = (
    "https://celestrak.org/SOCRATES/table-socrates.php"
    "?CATNR=25544&ORDER=MINRANGE&MAX=25"
)


class _SocratesParser:
    def __init__(self):
        from html.parser import HTMLParser
        self.rows: List[List[str]] = []
        self._row: List[str] = []
        self._cell: List[str] = []
        self._in_cell = False
        outer = self

        class _Inner(HTMLParser):
            def handle_starttag(self, tag, attrs):
                if tag == "tr":
                    outer._row = []
                elif tag in ("td", "th"):
                    outer._in_cell = True
                    outer._cell = []

            def handle_endtag(self, tag):
                if tag in ("td", "th"):
                    outer._in_cell = False
                    outer._row.append("".join(outer._cell).strip())
                elif tag == "tr":
                    if outer._row:
                        outer.rows.append(outer._row)

            def handle_data(self, data):
                if outer._in_cell:
                    outer._cell.append(data)

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
    try:
        r = session.get(SOCRATES_URL, timeout=20)
        r.raise_for_status()
    except Exception as e:
        return _envelope(
            items=[], source="Celestrak SOCRATES",
            source_url=SOCRATES_URL,
            error=f"{type(e).__name__}: {e}", mode="current",
            limitations="Только текущие сближения (7 суток вперёд, порог 5 км); архива нет.",
        )

    m = re.search(r"Data current as of\s*([^<]+)", r.text, re.IGNORECASE)
    data_current = m.group(1).strip() if m else None

    try:
        items = _parse_socrates_html(r.text)
    except Exception as e:
        return _envelope(
            items=[], source="Celestrak SOCRATES",
            source_url=SOCRATES_URL,
            error=f"parse: {type(e).__name__}: {e}", mode="current",
        )

    return _envelope(
        items=items, source="Celestrak SOCRATES",
        source_url=SOCRATES_URL,
        version=f"SOCRATES Plus (data current as of {data_current})" if data_current else "SOCRATES Plus",
        units="km / Pc", mode="current",
        limitations=(
            "Pc — консервативная верхняя оценка, не смешивать с Pc из CDM. "
            "Только текущее, архива нет."
        ),
    )


# ===========================================================================
# SEP-2 / SEP-3 / SEP-4 / G-2 / G-3. NOAA SWPC (current only)
# ===========================================================================

SWPC_BASE = "https://services.swpc.noaa.gov"


def _rows_from_swpc_json(raw) -> List[Dict[str, Any]]:
    if not raw:
        return []
    if isinstance(raw[0], dict):
        return list(raw)
    header, *rows = raw
    return [dict(zip(header, row)) for row in rows]


def fetch_swpc_protons() -> Dict[str, Any]:
    """SEP-2. Интегральные протоны GOES, канал ≥10 МэВ."""
    url = f"{SWPC_BASE}/json/goes/primary/integral-protons-7-day.json"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        return _envelope(
            items=[], source="NOAA SWPC protons",
            source_url=url, error=f"{type(e).__name__}: {e}", mode="current",
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
        source_url=url, version="integral-protons-7-day",
        units="pfu", mode="current",
        limitations="Только последние 7 суток; архива нет. Для replay — SEP-1 или SEP-5.",
    )


def fetch_swpc_kp() -> Dict[str, Any]:
    """G-2. Планетарный Kp (оценка по 8 из 13 станций)."""
    url = f"{SWPC_BASE}/products/noaa-planetary-k-index.json"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        return _envelope(
            items=[], source="NOAA SWPC Kp",
            source_url=url, error=f"{type(e).__name__}: {e}", mode="current",
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
            "Только последние ~7 суток."
        ),
    )


def fetch_swpc_kp_forecast() -> Dict[str, Any]:
    """G-3. Прогноз Kp по 3-часовым интервалам (observed + forecast)."""
    url = f"{SWPC_BASE}/products/noaa-planetary-k-index-forecast.json"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        return _envelope(
            items=[], source="NOAA SWPC Kp forecast",
            source_url=url, error=f"{type(e).__name__}: {e}", mode="current",
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
            "Смесь observed (прошлое) и forecast (будущее); фильтруйте "
            "по 'observed'. Обновляется несколько раз в сутки."
        ),
    )


def fetch_swpc_alerts() -> Dict[str, Any]:
    """SEP-3. Активные alerts/watches/warnings."""
    url = f"{SWPC_BASE}/products/alerts.json"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        return _envelope(
            items=[], source="NOAA SWPC alerts",
            source_url=url, error=f"{type(e).__name__}: {e}", mode="current",
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
            "Архива нет; для replay — SEP-5 (RSGA)."
        ),
    )


def fetch_swpc_3day_forecast() -> Dict[str, Any]:
    """SEP-4. 3-day forecast (текст)."""
    url = f"{SWPC_BASE}/text/3-day-forecast.txt"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
    except Exception as e:
        return _envelope(
            items=[], source="NOAA SWPC 3-day forecast",
            source_url=url, error=f"{type(e).__name__}: {e}", mode="current",
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
            "Выпускается в 00:30 и 12:30 UTC, плюс внеплановые."
        ),
    )


# ===========================================================================
# G-1. GFZ Potsdam — Kp (both: nowcast/current, def/historical)
# ===========================================================================

GFZ_KP_URL = "https://kp.gfz.de/app/json/"


def fetch_gfz_kp(
    start: datetime,
    end: datetime,
    *,
    cutoff: Optional[datetime] = None,
    lookback_days: int = 3,
) -> Dict[str, Any]:
    now = _now()
    is_historical = cutoff is not None
    if is_historical and (now - cutoff).days > 45:
        status = "def"
        version = "Kp_definitive"
        mode = "historical"
        limitations = (
            "Архивное значение (def): окончательные данные, не то, "
            "что было известно оператору. Для оперативных — G-2 (SWPC Kp)."
        )
    else:
        status = "all"
        version = "Kp_all"
        mode = "current"
        limitations = (
            "Смесь nowcast/definitive. GFZ не даёт прогноз вперёд; "
            "для будущих окон используйте G-3 (SWPC Kp forecast)."
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
            source_url=GFZ_KP_URL, error=f"{type(e).__name__}: {e}",
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
        source_url=r.url, version=version, units="Kp", mode=mode,
        publication_time=cutoff,
        limitations=limitations,
    )


# ===========================================================================
# SEP-6 / G-4. NASA DONKI (both)
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
    r = session.get(f"{DONKI_BASE}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _donki_windows(start: datetime, end: datetime) -> Iterable[Tuple[datetime, datetime]]:
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=DONKI_MAX_WINDOW_DAYS), end)
        yield cur, nxt
        cur = nxt


def _donki_cutoff(items, cutoff, field):
    if cutoff:
        items = [i for i in items
                 if i.get(field) and _parse_iso(i[field]) <= cutoff]
    return items


def fetch_donki_sep(start, end, *, cutoff=None) -> Dict[str, Any]:
    """SEP-6. DONKI SEP — каталог событий."""
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

    items = _donki_cutoff(items, cutoff, "submission_time")
    items = _dedup(items, "sep_id")
    return _envelope(
        items=items, source="NASA DONKI SEP",
        source_url=f"{DONKI_BASE}/SEP",
        version="DONKI SEP", units="—",
        mode="historical" if cutoff else "current",
        publication_time=cutoff,
        limitations=(
            "Каталог событий, не прогноз. Официальный прогноз — SEP-3/SEP-4. "
            "Replay — по submissionTime. Максимум 30 дней за запрос."
        ),
    )


def fetch_donki_notifications(start, end, *, cutoff=None) -> Dict[str, Any]:
    """SEP-6. DONKI notifications."""
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

    items = _donki_cutoff(items, cutoff, "message_issue_time")
    items = _dedup(items, "message_id")
    return _envelope(
        items=items, source="NASA DONKI notifications",
        source_url=f"{DONKI_BASE}/notifications",
        version="DONKI notifications", units="—",
        mode="historical" if cutoff else "current",
        publication_time=cutoff,
        limitations="Replay — по messageIssueTime. Максимум 30 дней за запрос.",
    )


def fetch_donki_gst(start, end, *, cutoff=None) -> Dict[str, Any]:
    """G-4. DONKI GST — геомагнитные бури."""
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

    items = _donki_cutoff(items, cutoff, "submission_time")
    items = _dedup(items, "gst_id")
    return _envelope(
        items=items, source="NASA DONKI GST",
        source_url=f"{DONKI_BASE}/GST",
        version="DONKI GST", units="Kp",
        mode="historical" if cutoff else "current",
        publication_time=cutoff,
        limitations="Replay — по submissionTime.",
    )


def fetch_donki_cmeanalysis(start, end, *, cutoff=None) -> Dict[str, Any]:
    """G-4. DONKI CMEAnalysis — WSA-ENLIL расчёт прихода CME (±7 ч)."""
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

    items = _donki_cutoff(items, cutoff, "submission_time")
    items = _dedup(items, "analysis_id")
    return _envelope(
        items=items, source="NASA DONKI CMEAnalysis",
        source_url=f"{DONKI_BASE}/CMEAnalysis",
        version="DONKI CMEAnalysis", units="—",
        mode="historical" if cutoff else "current",
        publication_time=cutoff,
        limitations=(
            "Интервал прихода CME передавать как интервал, а не точку. "
            "Replay — по submissionTime (= время завершения расчёта)."
        ),
    )


# ===========================================================================
# SEP-1. NOAA NCEI — GOES SGPS L2 avg5m (both, архив отстаёт ~7 суток)
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


def fetch_sep_goes_archive(
    start: datetime,
    end: datetime,
    *,
    cutoff: Optional[datetime] = None,
    sat: Optional[str] = None,
    channels: Iterable[str] = ("P7",),
    include_integral: bool = True,
) -> Dict[str, Any]:
    if not _HAS_NETCDF:
        return _envelope(
            items=[], source="NOAA NCEI GOES SGPS",
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
                    ch_names = _resolve_channel_names(diff)
                    for ch in channels:
                        idx = _resolve_channel_index(ch, ch_names)
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

    return _envelope(
        items=items, source=f"NOAA GOES-{sat} SGPS",
        source_url=urls[0] if urls else GOES_BASE.format(sat=sat, y=start.year, m=start.month),
        version="sgps-l2-avg5m v3-0-3", units="pfu (protons/cm^2 s sr)",
        publication_time=cutoff,
        error="; ".join(errors) if errors and not items else None,
        mode="historical" if cutoff else "current",
        limitations=(
            "SGPS L2 avg5m — 5-минутные средние. Версия файла берётся из листинга NCEI. "
            "Архив NCEI отстаёт ~7 суток; для свежего current используйте SEP-2. "
            "Дифференциальные каналы — по sensor_units[0] (west)."
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
            "time_utc": _iso_z(t), "kind": kind,
            "channel": channel, "flux_pfu": val,
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
    for i, name in enumerate(channel_names):
        if name == wanted.upper():
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
# SEP-5. NCEI RSGA — ежедневный отчёт и прогноз (both, архив для replay)
# ===========================================================================

RSGA_BASE = (
    "https://www.ngdc.noaa.gov/stp/space-weather/swpc-products/"
    "daily_reports/reports_solar_geophysical_activity/{y}/{m:02d}/"
)


def _rsga_url(day: datetime) -> str:
    fname = f"{day.strftime('%Y%m%d')}RSGA.txt"
    return RSGA_BASE.format(y=day.year, m=day.month) + fname


def _parse_rsga_issued(text: str, day: datetime) -> Optional[datetime]:
    """Извлекает время выпуска из заголовка RSGA.

    Варианты:
      :Issued: 2024 May 10 0030 UTC
      SDF Number 131 Issued at 2200Z on 09 May 2024
    """
    # :Issued: 2024 May 10 0030 UTC
    m = re.search(r":Issued:\s*(\d{4})\s+(\w+)\s+(\d{1,2})\s+(\d{4})\s*UTC", text)
    if m:
        try:
            dt = datetime.strptime(
                f"{m.group(1)} {m.group(2)} {m.group(3)} {m.group(4)}",
                "%Y %B %d %H%M",
            )
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    # SDF Number N Issued at HHMMZ on DD Mon YYYY
    m = re.search(
        r"Issued at\s+(\d{2})(\d{2})Z\s+on\s+(\d{1,2})\s+(\w+)\s+(\d{4})",
        text,
    )
    if m:
        try:
            dt = datetime.strptime(
                f"{m.group(5)} {m.group(4)} {m.group(3)} {m.group(1)}{m.group(2)}",
                "%Y %B %d %H%M",
            )
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    # Fallback: конец дня
    return day.replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)


def fetch_rsga_archive(
    start: datetime,
    end: datetime,
    *,
    cutoff: Optional[datetime] = None,
) -> Dict[str, Any]:
    """SEP-5. NCEI RSGA — ежедневные отчёты и прогнозы.

    Возвращает items: {day, issued, url, text}.
    Для строгого replay фильтрует по issued <= cutoff.
    """
    items: List[Dict[str, Any]] = []
    errors: List[str] = []
    urls: List[str] = []

    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= end:
        url = _rsga_url(day)
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 404:
                day += timedelta(days=1)
                continue
            r.raise_for_status()
        except Exception as e:
            errors.append(f"{day.date()}: {type(e).__name__}: {e}")
            day += timedelta(days=1)
            continue

        urls.append(url)
        issued = _parse_rsga_issued(r.text, day)
        if cutoff and issued and issued > cutoff:
            # Поздний выпуск, в replay не учитывается
            day += timedelta(days=1)
            continue

        items.append({
            "day": day.date().isoformat(),
            "issued": _iso_z(issued) if issued else None,
            "url": url,
            "text": r.text[:20000],
        })
        day += timedelta(days=1)

    items.sort(key=lambda x: x["issued"] or "")

    return _envelope(
        items=items, source="NOAA NCEI RSGA",
        source_url=urls[0] if urls else RSGA_BASE.format(y=start.year, m=start.month),
        version="RSGA", units="—",
        publication_time=cutoff,
        error="; ".join(errors) if errors and not items else None,
        mode="historical" if cutoff else "current",
        limitations=(
            "Ежедневный отчёт и прогноз SWPC. Replay — по issued (время выпуска). "
            "GEOALERT прекращён 2026-05-20; используем только архив."
        ),
    )


# ===========================================================================
# Реестр и агрегатор
# ===========================================================================

def list_registered_sources() -> List[Dict[str, Any]]:
    """Реестр в соответствии с Reestr_istochnikov_VKD.xlsx."""
    return [
        # --- SEP (космическая погода: протоны) ---
        {"id": "SEP-1", "factor": "SEP", "source": "NOAA NCEI GOES SGPS",
         "role": "архив наблюдений (диф. каналы)", "modes": ["current", "historical"], "ttl": 86400},
        {"id": "SEP-2", "factor": "SEP", "source": "NOAA SWPC",
         "role": "оперативные протоны", "modes": ["current"], "ttl": 300},
        {"id": "SEP-3", "factor": "SEP/G", "source": "NOAA SWPC",
         "role": "оповещения alerts", "modes": ["current"], "ttl": 300},
        {"id": "SEP-4", "factor": "SEP/G", "source": "NOAA SWPC",
         "role": "3-day forecast", "modes": ["current"], "ttl": 3600},
        {"id": "SEP-5", "factor": "SEP/G", "source": "NOAA NCEI RSGA",
         "role": "архив сводок и прогнозов (replay)", "modes": ["current", "historical"], "ttl": 86400},
        {"id": "SEP-6", "factor": "SEP", "source": "NASA DONKI",
         "role": "каталог событий + notifications", "modes": ["current", "historical"], "ttl": 600},

        # --- G (геомагнитные бури) ---
        {"id": "G-1", "factor": "G", "source": "GFZ Potsdam",
         "role": "Kp архив/nowcast", "modes": ["current", "historical"], "ttl": 1800},
        {"id": "G-2", "factor": "G", "source": "NOAA SWPC",
         "role": "Kp оперативный", "modes": ["current"], "ttl": 300},
        {"id": "G-3", "factor": "G", "source": "NOAA SWPC",
         "role": "Kp прогноз", "modes": ["current"], "ttl": 900},
        {"id": "G-4", "factor": "G", "source": "NASA DONKI",
         "role": "CMEAnalysis + GST", "modes": ["current", "historical"], "ttl": 600},

        # --- SAA (орбита) ---
        {"id": "SAA-1", "factor": "orbit", "source": "Space-Track gp_history",
         "role": "архив TLE", "modes": ["historical"], "ttl": 86400},
        {"id": "SAA-2", "factor": "orbit", "source": "CelesTrak GP",
         "role": "оперативный TLE", "modes": ["current"], "ttl": 7200},
        {"id": "SAA-3", "factor": "orbit", "source": "SGP4 (расчёт)",
         "role": "пропагатор", "modes": ["calculation"], "ttl": 0},
        {"id": "SAA-4", "factor": "orbit", "source": "IGRF-14 (расчёт)",
         "role": "модель поля", "modes": ["calculation"], "ttl": 0},

        # --- CONJ (сближения) ---
        {"id": "CONJ-1", "factor": "conjunction", "source": "Space-Track cdm_public",
         "role": "архив CDM", "modes": ["historical"], "ttl": 1800},
        {"id": "CONJ-2", "factor": "conjunction", "source": "CelesTrak SOCRATES",
         "role": "текущие сближения", "modes": ["current"], "ttl": 3600},
        {"id": "CONJ-3", "factor": "conjunction", "source": "Space-Track cdm_public",
         "role": "будущие TCA", "modes": ["current"], "ttl": 1800},
        {"id": "CONJ-4", "factor": "conjunction", "source": "расчёт по GP_HISTORY",
         "role": "собственный расчёт (replay)", "modes": ["calculation"], "ttl": 0},
    ]


def collect_for_window(
    start: datetime,
    end: datetime,
    *,
    cutoff: Optional[datetime] = None,
    include_historical_tle: bool = False,
    include_cdm: bool = True,
    include_sep: bool = True,
    include_swpc: bool = True,
    include_donki: bool = True,
    include_gfz: bool = True,
    include_socrates: bool = False,
    include_rsga: bool = True,
) -> Dict[str, Any]:
    """Собирает источники для окна [start, end].

    cutoff задан → historical. Live-only источники (SWPC, SOCRATES,
    CelesTrak GP) автоматически пропускаются (защита T4).
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

    def _skip(name: str) -> None:
        result["skipped_live_only"].append(name)

    # --- SAA-1 / SAA-2: орбита ---
    if is_historical:
        if include_historical_tle:
            _try("tle_history", fetch_tle_history, start, end, cutoff=cutoff)
    else:
        _try("tle", fetch_tle_iss)

    # --- CONJ-1 / CONJ-3: сближения ---
    if include_cdm:
        _try("conjunctions", fetch_conjunctions, start, end, cutoff=cutoff)

    # --- CONJ-2: SOCRATES (current only) ---
    if include_socrates:
        if is_historical:
            _skip("socrates")
        else:
            _try("socrates", fetch_socrates)

    # --- SEP-1: GOES SGPS (both) ---
    if include_sep:
        _try("sep_goes_archive", fetch_sep_goes_archive, start, end, cutoff=cutoff)

    # --- SEP-2: SWPC protons (current only) ---
    if include_sep and include_swpc:
        if is_historical:
            _skip("sep_swpc_protons")
        else:
            _try("sep_swpc_protons", fetch_swpc_protons)

    # --- SEP-3, SEP-4, G-2, G-3: SWPC live-only ---
    if include_swpc:
        for name, fn in (
            ("swpc_alerts", fetch_swpc_alerts),           # SEP-3
            ("swpc_3day", fetch_swpc_3day_forecast),      # SEP-4
            ("swpc_kp", fetch_swpc_kp),                   # G-2
            ("swpc_kp_forecast", fetch_swpc_kp_forecast), # G-3
        ):
            if is_historical:
                _skip(name)
            else:
                _try(name, fn)

    # --- SEP-5: RSGA (both) ---
    if include_rsga:
        _try("rsga", fetch_rsga_archive, start, end, cutoff=cutoff)

    # --- G-1: GFZ Kp (both) ---
    if include_gfz:
        _try("gfz_kp", fetch_gfz_kp, start, end, cutoff=cutoff)

    # --- SEP-6 / G-4: DONKI (both) ---
    if include_donki:
        _try("donki_sep", fetch_donki_sep, start, end, cutoff=cutoff)          # SEP-6
        _try("donki_notif", fetch_donki_notifications, start, end, cutoff=cutoff)  # SEP-6
        _try("donki_gst", fetch_donki_gst, start, end, cutoff=cutoff)          # G-4
        _try("donki_cme", fetch_donki_cmeanalysis, start, end, cutoff=cutoff)  # G-4

    return result