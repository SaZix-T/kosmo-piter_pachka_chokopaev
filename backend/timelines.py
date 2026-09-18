from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Tuple, Dict, Any


def parse_dt(value: datetime | str) -> datetime:
    """Parse datetime or ISO8601 string to aware UTC datetime.
    Accepts 'YYYY-MM-DD HH:MM' (UTC assumed) or ISO8601 with 'Z' or offset.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    s = value.strip()
    try:
        if 'T' in s or '+' in s or s.endswith('Z'):
            if s.endswith('Z'):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
        else:
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        # Fallback to now in UTC
        return datetime.now(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class Impact:
    start: datetime
    end: datetime
    # severity for segment coloring: 'good' | 'warn' | 'bad'
    level: str
    # optional threat classification and label for listing below
    threat_severity: Optional[str] = None  # 'critical' | 'minor'
    threat_label: Optional[str] = None


def _overlap(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> Optional[Tuple[datetime, datetime]]:
    lo = max(a0, b0)
    hi = min(a1, b1)
    return (lo, hi) if lo < hi else None


def _worst_level(levels: Iterable[str]) -> str:
    rank = {"good": 0, "warn": 1, "bad": 2}
    worst = 0
    for lv in levels:
        worst = max(worst, rank.get(lv, 0))
    for name, val in rank.items():
        if val == worst:
            return name
    return "good"


def build_window_timeline(window_start: datetime | str, window_end: datetime | str, impacts: Iterable[Dict[str, Any] | Impact]) -> Dict[str, Any]:
    """Build a single window timeline from impacts.

    impacts: iterable of dict or Impact with keys:
      - start, end: datetime or iso string
      - level: 'good'|'warn'|'bad'
      - threat_severity: 'critical'|'minor' (optional)
      - threat_label: str (optional)

    Returns dict with keys: start, end, duration_minutes, segments[{start,end,level}],
    weather_status, threats_critical[], threats_minor[]
    """
    ws = parse_dt(window_start)
    we = parse_dt(window_end)
    if we <= ws:
        we = ws + timedelta(minutes=60)

    # Normalize impacts and clip to window
    norm_impacts: List[Impact] = []
    for imp in impacts:
        if isinstance(imp, Impact):
            i = imp
        else:
            i = Impact(
                start=parse_dt(imp["start"]),
                end=parse_dt(imp["end"]),
                level=str(imp.get("level", "bad")),
                threat_severity=imp.get("threat_severity"),
                threat_label=imp.get("threat_label"),
            )
        ov = _overlap(ws, we, i.start, i.end)
        if ov:
            s, e = ov
            norm_impacts.append(Impact(start=s, end=e, level=i.level, threat_severity=i.threat_severity, threat_label=i.threat_label))

    # Build boundary points
    boundaries = {ws, we}
    for i in norm_impacts:
        boundaries.add(i.start)
        boundaries.add(i.end)
    points = sorted(boundaries)

    segments: List[Dict[str, Any]] = []
    levels_in_window: List[str] = []
    for a, b in zip(points[:-1], points[1:]):
        # Determine level for segment [a,b]
        active = [imp for imp in norm_impacts if (imp.start < b and imp.end > a)]
        if not active:
            level = "good"
        else:
            level = _worst_level(imp.level for imp in active)
        segments.append({"start": iso_z(a), "end": iso_z(b), "level": level})
        levels_in_window.append(level)

    weather_status = _worst_level(levels_in_window) if levels_in_window else "good"

    # Threats lists
    crit_labels = []
    minor_labels = []
    seen_crit = set()
    seen_minor = set()
    for imp in norm_impacts:
        label = imp.threat_label or imp.level
        if imp.threat_severity == "critical":
            if label not in seen_crit:
                seen_crit.add(label)
                crit_labels.append(label)
        elif imp.threat_severity == "minor":
            if label not in seen_minor:
                seen_minor.add(label)
                minor_labels.append(label)

    return {
        "start": iso_z(ws),
        "end": iso_z(we),
        "duration_minutes": int((we - ws).total_seconds() // 60),
        "segments": segments,
        "weather_status": weather_status,
        "threats_critical": crit_labels,
        "threats_minor": minor_labels,
    }


def build_windows_timelines(windows: Iterable[Dict[str, Any]], impacts: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build timelines for multiple windows given a list of impacts.

    windows: iterable of dicts with keys:
      - start: datetime or iso string
      - end or duration_minutes: specify end or duration
      - optional id/label preserved in output.

    impacts: list of intervals affecting conditions (shared for all windows).
    """
    result: List[Dict[str, Any]] = []
    for idx, w in enumerate(windows, start=1):
        ws = parse_dt(w["start"])  # required
        if "end" in w and w["end"]:
            we = parse_dt(w["end"])
        else:
            dur = int(w.get("duration_minutes", 60))
            we = ws + timedelta(minutes=dur)
        built = build_window_timeline(ws, we, impacts)
        built["id"] = w.get("id", idx)
        result.append(built)
    return result
