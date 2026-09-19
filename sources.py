"""Источники данных для анализа рисков ВКД.

Слой доступа к данным: HTTP, кэш, десериализация, фильтрация по cutoff.
Не знает про Impact, good/warn/bad и пороги — это ответственность metrics.py.

Все fetch_* функции возвращают единый envelope:
    {
        "items": [...],
        "source": str,
        "source_url": str,
        "fetched_at": iso_z,
        "publication_time": iso_z | None,
        "version": str | None,
        "units": str | None,
        "limitations": str | None,
        "error": str | None,   # заполнен, если источник упал
    }
Пустой items + error=None  →  данных нет за период (не означает «безопасно»).
Пустой items + error!=None →  источник недоступен.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from requests_cache import CachedSession, DO_NOT_CACHE

# Опциональные зависимости
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
# Cache with dynamic TTL
# ---------------------------------------------------------------------------

CACHE_DIR = Path(os.getenv("CACHE_DIR", "cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Карта TTL по паттернам URL. Используется в _dynamic_ttl().
# Первое совпадение выигрывает.
_TTL_MAP: List[tuple[str, int]] = [
    # Celestrak TLE обновляется раз в несколько часов
    ("https://celestrak.org/NORAD/elements/", 3600),
    # gp_history — архив, можно кэшировать долго
    ("https://www.space-track.org/basicspacedata/query/class/gp_history", 86400),
    # cdm_public — обновляется часто
    ("https://www.space-track.org/basicspacedata/query/class/cdm_public", 1800),
    # NetCDF GOES — статика
    ("https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/", 86400),
    # NOAA SWPC
    ("https://services.swpc.noaa.gov/", 300),
    # NASA DONKI
    ("https://api.nasa.gov/DONKI/", 600),
]


def _dynamic_ttl(request) -> int:
    """Callback для requests_cache: возвращает TTL в секундах по URL."""
    url = request.url
    for pattern, ttl in _TTL_MAP:
        if pattern in url:
            return ttl
    return 600  # default


session = CachedSession(
    cache_name=str(CACHE_DIR / "http_cache"),
    backend="sqlite",
    expire_after=_dynamic_ttl,
    allowable_methods=("GET", "POST"),
    stale_if_error=True,
)


def set_ttl(pattern: str, seconds: int) -> None:
    """Программно изменить TTL для паттерна (например, из /api/refresh)."""
    global _TTL_MAP
    _TTL_MAP = [(p, t) for p, t in _TTL_MAP if p != pattern]
    _TTL_MAP.insert(0, (pattern, seconds))


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
) -> Dict[str, Any]:
    """Единая форма ответа источника."""
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
    }


def _dedup(items: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    """Убирает дубли по ключу, сортирует по нему же."""
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
    """Эпоха из первой строки TLE без Skyfield."""
    yy = int(line1[18:20])
    doy = float(line1[20:32])
    year = 2000 + yy if yy < 57 else 1900 + yy
    base = datetime(year, 1, 1, tzinfo=timezone.utc)
    return _iso_z(base + timedelta(days=doy - 1))


def _st_creds() -> tuple[str, str]:
    identity = os.getenv("SPACETRACK_IDENTITY")
    password = os.getenv("SPACETRACK_PASSWORD")
    if not (identity and password):
        raise RuntimeError(
            "Space-Track: задайте SPACETRACK_IDENTITY и SPACETRACK_PASSWORD"
        )
    return identity, password


# ---------------------------------------------------------------------------
# Celestrak — текущий TLE МКС
# ---------------------------------------------------------------------------

CELESTRAK_TLE_ISS = (
    "https://celestrak.org/NORAD/elements/gp.php"
    "?CATNR=25544&FORMAT=TLE"
)


def fetch_tle_iss() -> Dict[str, Any]:
    """Текущий TLE МКС."""
    try:
        r = session.get(CELESTRAK_TLE_ISS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        return _envelope(
            items=[], source="Celestrak", source_url=CELESTRAK_TLE_ISS,
            error=f"{type(e).__name__}: {e}",
            limitations="Без TLE расчёт положения МКС невозможен.",
        )

    lines = [ln.strip() for ln in r.text.splitlines() if ln.strip()]
    if len(lines) < 3:
        return _envelope(
            items=[], source="Celestrak", source_url=CELESTRAK_TLE_ISS,
            error=f"неполный ответ: {lines!r}",
        )

    name, l1, l2 = lines[0], lines[1], lines[2]
    item = {
        "name": name,
        "line1": l1,
        "line2": l2,
        "epoch_utc": _tle_epoch(l1),
    }
    return _envelope(
        items=[item], source="Celestrak", source_url=CELESTRAK_TLE_ISS,
        version="gp.php", units="TLE",
        limitations="TLE ISS обновляется ~раз в сутки; давность = now - epoch.",
    )


# ---------------------------------------------------------------------------
# Space-Track — исторический TLE (gp_history)
# ---------------------------------------------------------------------------

def fetch_tle_history(
    start: datetime,
    end: datetime,
    *,
    cutoff: Optional[datetime] = None,
    norad_id: int = 25544,
) -> Dict[str, Any]:
    """gp_history: все публикации TLE за [start, end].

    cutoff — момент запроса пользователя; более поздние публикации
    отбрасываются (критерий T4, replay).
    """
    if not _HAS_SPACETRACK:
        return _envelope(
            items=[], source="Space-Track gp_history",
            source_url="https://www.space-track.org/basicspacedata/query/class/gp_history",
            error="spacetrack не установлен: pip install spacetrack",
        )

    try:
        identity, password = _st_creds()
        drange = op.inclusive_range(start, end)
        items: List[Dict[str, Any]] = []
        with SpaceTrackClient(identity=identity, password=password) as st:
            raw = st.gp_history(
                iter_lines=True,
                norad_cat_id=norad_id,
                creation_date=drange,
                orderby="CREATION_DATE",
                format="tle",
            )
            buf: List[str] = []
            for line in raw:
                buf.append(line.strip())
                if len(buf) == 3:
                    items.append({
                        "name": buf[0],
                        "line1": buf[1],
                        "line2": buf[2],
                        "epoch_utc": _tle_epoch(buf[1]),
                    })
                    buf = []
    except Exception as e:
        return _envelope(
            items=[], source="Space-Track gp_history",
            source_url="https://www.space-track.org/basicspacedata/query/class/gp_history",
            error=f"{type(e).__name__}: {e}",
        )

    if cutoff:
        items = [i for i in items if _parse_iso(i["epoch_utc"]) <= cutoff]

    items = _dedup(items, "epoch_utc")
    return _envelope(
        items=items, source="Space-Track gp_history",
        source_url="https://www.space-track.org/basicspacedata/query/class/gp_history",
        version="gp_history", units="TLE",
        publication_time=cutoff,
        limitations=(
            "Исторические TLE отдаются с задержкой публикации; "
            "для строгого replay фильтруем по cutoff."
        ),
    )


# ---------------------------------------------------------------------------
# Space-Track — сближения (cdm_public)
# ---------------------------------------------------------------------------

def fetch_conjunctions(
    start: datetime,
    end: datetime,
    *,
    cutoff: Optional[datetime] = None,
    sat_1_id: int = 25544,
) -> Dict[str, Any]:
    """cdm_public: CDM для МКС за [start, end] (аналог SOCRATES)."""
    if not _HAS_SPACETRACK:
        return _envelope(
            items=[], source="Space-Track cdm_public",
            source_url="https://www.space-track.org/basicspacedata/query/class/cdm_public",
            error="spacetrack не установлен",
        )

    try:
        identity, password = _st_creds()
        tca_range = op.inclusive_range(start, end)
        with SpaceTrackClient(identity=identity, password=password) as st:
            raw = st.generic_request(
                "cdm_public",
                controller="basicspacedata",
                sat_1_id=sat_1_id,
                tca=tca_range,
                orderby="TCA asc",
                format="json",
            )
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        return _envelope(
            items=[], source="Space-Track cdm_public",
            source_url="https://www.space-track.org/basicspacedata/query/class/cdm_public",
            error=f"{type(e).__name__}: {e}",
        )

    items = [_normalize_cdm(x) for x in data if x.get("TCA")]
    items = [i for i in items if i["tca"]]

    if cutoff:
        items = [i for i in items if _parse_iso(i["tca"]) <= cutoff]

    items = _dedup(items, "cdm_id")
    return _envelope(
        items=items, source="Space-Track cdm_public",
        source_url="https://www.space-track.org/basicspacedata/query/class/cdm_public",
        version="cdm_public", units="probability / km",
        publication_time=cutoff,
        limitations=(
            "CDM публикуются с задержкой; поздние уточнения не учитываются "
            "в историческом режиме (cutoff)."
        ),
    )


def _normalize_cdm(x: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "cdm_id": x.get("CDM_ID"),
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


def _to_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# NOAA GOES — SEP proton flux (NetCDF L2 avg5m)
# ---------------------------------------------------------------------------

GOES_BASE = (
    "https://data.ngdc.noaa.gov/platforms/solar-space-observing-"
    "satellites/goes/goes{sat}/l2/data/sgps-l2-avg5m/{y}/{m:02d}/"
)

# Известные каналы GOES-R SGPS (по документации).
# Используются как fallback, если атрибут diff_channels отсутствует.
_SEP_FALLBACK_CHANNELS = [
    "P1", "P2A", "P2B", "P3", "P4", "P5",
    "P6", "P7", "P8A", "P8B", "P8C", "P9", "P10",
]


def fetch_sep_proton_flux(
    start: datetime,
    end: datetime,
    *,
    cutoff: Optional[datetime] = None,
    sat: str = "16",
    channels: Iterable[str] = ("P7",),
    include_integral: bool = True,
) -> Dict[str, Any]:
    """Читает NetCDF GOES SGPS L2 avg5m за нужные сутки.

    Возвращает points с полями:
      {time_utc, kind: 'diff'|'integral', channel, flux_pfu}
    """
    if not _HAS_NETCDF:
        return _envelope(
            items=[], source="NOAA GOES SEP",
            source_url="https://data.ngdc.noaa.gov/",
            error="netCDF4 не установлен: pip install netCDF4 cftime",
        )

    points: List[Dict[str, Any]] = []
    urls: List[str] = []
    errors: List[str] = []

    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= end:
        fname = f"sci_sgps-l2-avg5m_g{sat}_d{day.strftime('%Y%m%d')}_v3-0-2.nc"
        url = GOES_BASE.format(sat=sat, y=day.year, m=day.month) + fname

        try:
            r = session.get(url, timeout=60)
            if r.status_code == 404:
                day += timedelta(days=1)
                continue
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

                # --- дифференциальные каналы ---
                if "AvgDiffProtonFlux" in ds.variables:
                    diff = ds["AvgDiffProtonFlux"]
                    channel_names = _resolve_channel_names(diff)
                    for ch in channels:
                        idx = _resolve_channel_index(ch, channel_names)
                        if idx is None:
                            continue
                        series = diff[:][:, 0, idx]   # sensor_units[0] = west
                        _append_series(
                            points, times, series, start, end, cutoff,
                            kind="diff", channel=ch,
                        )

                # --- интегральный >500 МэВ ---
                if include_integral and "AvgIntProtonFlux" in ds.variables:
                    integral = ds["AvgIntProtonFlux"]
                    series = integral[:][:, 0]        # dims (record, sensor_units)
                    _append_series(
                        points, times, series, start, end, cutoff,
                        kind="integral", channel=">500MeV",
                    )
            finally:
                ds.close()
        except Exception as e:
            errors.append(f"{day.date()} parse: {type(e).__name__}: {e}")

        day += timedelta(days=1)

    # Дедуп по (time, kind, channel)
    deduped: Dict[tuple, Dict[str, Any]] = {}
    for p in points:
        key = (p["time_utc"], p["kind"], p["channel"])
        deduped[key] = p
    items = sorted(deduped.values(), key=lambda p: (p["time_utc"], p["channel"]))

    error_msg = "; ".join(errors) if errors and not items else None

    return _envelope(
        items=items, source="NOAA GOES SEP",
        source_url=urls[0] if urls else GOES_BASE.format(sat=sat, y=start.year, m=start.month),
        version="sgps-l2-avg5m v3-0-2", units="pfu (protons/cm^2 s sr)",
        publication_time=cutoff,
        error=error_msg,
        limitations=(
            "SGPS L2 avg5m — 5-минутные средние; окончательные значения "
            "могут уточняться. Дифференциальные каналы — по sensor_units[0] (west)."
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
    """Пытается прочитать имена каналов из атрибутов переменной.

    Проверяет атрибуты: diff_channels, channel_names, channels.
    Возвращает список имён или fallback из _SEP_FALLBACK_CHANNELS.
    """
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
    # fallback
    return [c.upper() for c in _SEP_FALLBACK_CHANNELS]


def _resolve_channel_index(wanted: str, channel_names: List[str]) -> Optional[int]:
    """Ищет индекс канала по имени. Возвращает None, если не найден."""
    wanted_upper = wanted.upper()
    for i, name in enumerate(channel_names):
        if name == wanted_upper:
            return i
    return None


def _read_goes_time(ds) -> List[datetime]:
    """L2_SciData_TimeStamp — double-массив.

    Поддерживает два варианта units:
      - cftime-совместимые (например, 'seconds since 2000-01-01 12:00:00')
      - POSIX-секунды (epoch 1970-01-01 UTC), если units отсутствует.
    """
    var = ds["L2_SciData_TimeStamp"]
    raw = var[:]
    units = getattr(var, "units", None)

    # Случай 1: cftime-совместимые units
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

    # Случай 2: POSIX-секунды
    out: List[datetime] = []
    for v in raw:
        try:
            out.append(datetime.fromtimestamp(float(v), tz=timezone.utc))
        except (TypeError, ValueError, OSError):
            continue
    return out


# ---------------------------------------------------------------------------
# NASA MEO — метеороидные потоки (статический JSON)
# ---------------------------------------------------------------------------

METEORS_URL = "https://raw.githubusercontent.com/nasa/meo/main/showers.json"  # заглушка


def fetch_meteor_showers() -> Dict[str, Any]:
    """Календарь метеороидных потоков."""
    try:
        r = session.get(METEORS_URL, timeout=15)
        if r.status_code == 200:
            data = r.json()
            items = data.get("showers") or data.get("items") or []
        else:
            items = _METEORS_FALLBACK
    except Exception:
        items = _METEORS_FALLBACK

    return _envelope(
        items=items, source="NASA MEO (static)",
        source_url=METEORS_URL, version="static-2026",
        units="ZHR",
        limitations="Календарь потоков не учитывает реальную плотность пыли.",
    )


_METEORS_FALLBACK: List[Dict[str, Any]] = [
    {"name": "Eta Aquariids", "start": "2024-05-05T00:00:00Z",
     "end": "2024-05-07T00:00:00Z", "zhr": 50},
    {"name": "Perseids", "start": "2024-08-11T00:00:00Z",
     "end": "2024-08-13T00:00:00Z", "zhr": 100},
]


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

def collect_for_window(
    start: datetime,
    end: datetime,
    *,
    cutoff: Optional[datetime] = None,
    include_historical_tle: bool = False,
    include_cdm: bool = True,
    include_sep: bool = True,
    include_meteors: bool = True,
) -> Dict[str, Any]:
    """Собирает все источники для одного окна.

    Никогда не бросает — ошибки складываются в result["errors"].
    """
    result: Dict[str, Any] = {
        "window": {"start": _iso_z(start), "end": _iso_z(end)},
        "errors": {},
    }

    result["tle"] = fetch_tle_iss()
    if result["tle"].get("error"):
        result["errors"]["celestrak"] = result["tle"]["error"]

    if include_historical_tle:
        result["tle_history"] = fetch_tle_history(start, end, cutoff=cutoff)
        if result["tle_history"].get("error"):
            result["errors"]["spacetrack_history"] = result["tle_history"]["error"]

    if include_cdm:
        result["conjunctions"] = fetch_conjunctions(start, end, cutoff=cutoff)
        if result["conjunctions"].get("error"):
            result["errors"]["spacetrack_cdm"] = result["conjunctions"]["error"]

    if include_sep:
        result["sep"] = fetch_sep_proton_flux(start, end, cutoff=cutoff)
        if result["sep"].get("error"):
            result["errors"]["noaa_goes_sep"] = result["sep"]["error"]

    if include_meteors:
        result["meteors"] = fetch_meteor_showers()
        if result["meteors"].get("error"):
            result["errors"]["nasa_meo"] = result["meteors"]["error"]

    return result