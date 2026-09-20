"""Расчёт положения МКС для 3D-карты.

Использует skyfield (уже в requirements). Возвращает геодезические
координаты (lat, lon, alt) для каждого момента времени.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from skyfield.api import EarthSatellite, load, wgs84

_ts = load.timescale()


def track(
    line1: str,
    line2: str,
    start: datetime,
    end: datetime,
    step_minutes: int = 10,
    max_points: int = 250,
) -> List[Dict]:
    """Возвращает список точек {t, lat, lon, alt_km} за [start, end].

    Шаг автоматически увеличивается, чтобы не превысить max_points.
    """
    if end <= start:
        return []

    total_min = (end - start).total_seconds() / 60.0
    step = max(step_minutes, int(total_min / max_points) or 1)

    try:
        sat = EarthSatellite(line1, line2, ts=_ts)
    except Exception:
        return []

    points: List[Dict] = []
    t = start
    step_delta = timedelta(minutes=step)
    while t <= end:
        try:
            tt = _ts.from_datetime(t)
            geo = sat.at(tt)
            sub = wgs84.subpoint(geo)
            points.append({
                "t": t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "lat": float(sub.latitude.degrees),
                "lon": float(sub.longitude.degrees),
                "alt_km": float(sub.elevation.km),
            })
        except Exception:
            pass
        t += step_delta

    return points