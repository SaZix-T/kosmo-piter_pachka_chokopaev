"""Расчёт положения МКС по TLE.

Использует skyfield + sgp4. Возвращает геодезические координаты
и трек на интервале.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from skyfield.api import EarthSatellite, load, wgs84

_TS = None


def _ts():
    global _TS
    if _TS is None:
        _TS = load.timescale()
    return _TS


def iss_position(line1: str, line2: str, t: datetime) -> Dict[str, Any]:
    """Позиция МКС в момент t."""
    ts = _ts()
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    sat = EarthSatellite(line1, line2, "ISS", ts)
    tt = ts.from_datetime(t)
    geocentric = sat.at(tt)
    sub = wgs84.subpoint(geocentric)
    return {
        "time": t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "lat": float(sub.latitude.degrees),
        "lon": float(sub.longitude.degrees),
        "alt_km": float(sub.elevation.km),
    }


def iss_track(
    line1: str,
    line2: str,
    start: datetime,
    end: datetime,
    *,
    step_seconds: int = 60,
) -> List[Dict[str, Any]]:
    """Трек МКС от start до end с шагом step_seconds."""
    ts = _ts()
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    sat = EarthSatellite(line1, line2, "ISS", ts)
    out: List[Dict[str, Any]] = []
    t = start
    step = timedelta(seconds=step_seconds)
    while t <= end:
        tt = ts.from_datetime(t)
        geocentric = sat.at(tt)
        sub = wgs84.subpoint(geocentric)
        out.append({
            "time": t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "lat": float(sub.latitude.degrees),
            "lon": float(sub.longitude.degrees),
            "alt_km": float(sub.elevation.km),
        })
        t += step
    return out