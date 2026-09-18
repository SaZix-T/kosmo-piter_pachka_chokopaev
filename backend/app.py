from datetime import datetime, timezone
from typing import Optional

import httpx
from flask import Flask, jsonify, render_template, request
from skyfield.api import EarthSatellite, load
from timelines import build_window_timeline, build_windows_timelines, iso_z

CELESTRAK_STATIONS = "https://celestrak.org/NORAD/elements/stations.txt"
ISS_NAME = "ISS (ZARYA)"

app = Flask(__name__)

_tle_cache: tuple[str, str, datetime] | None = None


def isoformat_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


async def fetch_tle_iss() -> tuple[str, str, datetime]:
    global _tle_cache
    try:
        async with httpx.AsyncClient(headers={"User-Agent": "eva-mvp/0.2"}) as client:
            resp = await client.get(CELESTRAK_STATIONS, timeout=httpx.Timeout(10.0))
            resp.raise_for_status()
            text = resp.text
    except Exception:
        if _tle_cache:
            return _tle_cache
        # Try local fallback
        try:
            with open(app.root_path + "/data/iss_fallback.tle", "r") as f:
                lines_f = [ln.strip() for ln in f.readlines() if ln.strip()]
                l1, l2 = lines_f[1], lines_f[2]
                ts = load.timescale()
                sat = EarthSatellite(l1, l2, ISS_NAME, ts)
                epoch_dt = datetime(
                    sat.epoch.utc.year,
                    sat.epoch.utc.month,
                    sat.epoch.utc.day,
                    sat.epoch.utc.hour,
                    sat.epoch.utc.minute,
                    int(sat.epoch.utc.second),
                    tzinfo=timezone.utc,
                )
                _tle_cache = (l1, l2, epoch_dt)
                return _tle_cache
        except Exception as e:
            raise RuntimeError(f"Failed to fetch TLE and no fallback available: {e}")

    # Parse stations.txt format: name\nline1\nline2\n...
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i in range(0, len(lines) - 2):
        if lines[i].upper().startswith("ISS") and "ZARYA" in lines[i].upper():
            l1 = lines[i + 1]
            l2 = lines[i + 2]
            ts = load.timescale()
            sat = EarthSatellite(l1, l2, ISS_NAME, ts)
            epoch_dt = datetime(
                sat.epoch.utc.year,
                sat.epoch.utc.month,
                sat.epoch.utc.day,
                sat.epoch.utc.hour,
                sat.epoch.utc.minute,
                int(sat.epoch.utc.second),
                tzinfo=timezone.utc,
            )
            _tle_cache = (l1, l2, epoch_dt)
            return _tle_cache

    if _tle_cache:
        return _tle_cache
    raise RuntimeError("ISS TLE not found in stations.txt")


def parse_input_time(text: Optional[str]) -> datetime:
    if not text:
        return datetime.now(timezone.utc)
    s = text.strip()
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
        return datetime.now(timezone.utc)


@app.get("/")
def index():
    now = datetime.now(timezone.utc)
    return render_template("index.html", start_value=now.strftime("%Y-%m-%d %H:%M"), duration_value=6)


@app.post("/api/iss/position")
def api_iss_position():
    at = request.form.get("at") or request.args.get("at")
    when = parse_input_time(at)
    # Flask is sync; we use async client via anyio. For simplicity, call asyncio.run here.
    import asyncio as aio
    l1, l2, epoch_dt = aio.run(fetch_tle_iss())
    ts = load.timescale()
    sat = EarthSatellite(l1, l2, ISS_NAME, ts)
    t = ts.from_datetime(when)
    subpoint = sat.at(t).subpoint()
    lat_deg = float(subpoint.latitude.degrees)
    lon_deg = float(subpoint.longitude.degrees)
    alt_km = float(subpoint.elevation.km)
    epoch_age = abs((when - epoch_dt).total_seconds()) / 60.0
    stale = epoch_age > (24 * 60)
    return jsonify({
        "lat_deg": lat_deg,
        "lon_deg": lon_deg,
        "alt_km": alt_km,
        "timestamp_utc": isoformat_z(when),
        "tle_epoch_utc": isoformat_z(epoch_dt),
        "epoch_age_min": epoch_age,
        "source_url": CELESTRAK_STATIONS,
        "stale": stale,
    })


@app.post("/api/iss/now")
def api_now():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return jsonify({"now": now})


@app.post("/api/eva/windows")
def api_eva_windows():
    """Return demo EVA windows with segments and threat summaries.
    Input: form or query params 'start' (time), 'duration' (hours), 'count' (int)
    """
    start_txt = request.form.get("start") or request.args.get("start")
    duration_txt = request.form.get("duration") or request.args.get("duration") or "6"
    count_txt = request.form.get("count") or request.args.get("count") or "2"
    try:
        duration_h = max(1, min(8, int(float(duration_txt))))
    except Exception:
        duration_h = 6
    try:
        count = max(1, min(4, int(count_txt)))
    except Exception:
        count = 2

    base_start = parse_input_time(start_txt)
    total_min = duration_h * 60

    from datetime import timedelta
    windows = []
    for i in range(count):
        w_start = base_start + timedelta(minutes=90 * i)
        w_end = w_start + timedelta(minutes=total_min)
        windows.append({"id": i + 1, "start": iso_z(w_start), "end": iso_z(w_end), "duration_minutes": total_min})

    # Demo impacts affecting all windows (replace with real weather intervals later)
    impacts = [
        {"start": iso_z(base_start + timedelta(minutes=0)), "end": iso_z(base_start + timedelta(minutes=total_min * 0.1)), "level": "bad", "threat_severity": "minor", "threat_label": "Повышенный Kp"},
        {"start": iso_z(base_start + timedelta(minutes=total_min * 0.1)), "end": iso_z(base_start + timedelta(minutes=total_min * 0.7)), "level": "good"},
        {"start": iso_z(base_start + timedelta(minutes=total_min * 0.7)), "end": iso_z(base_start + timedelta(minutes=total_min)), "level": "bad", "threat_severity": "critical", "threat_label": "Геомагнитная буря"},
    ]

    built = build_windows_timelines(windows, impacts)
    return jsonify({"windows": built})


@app.get("/eva")
def eva_page():
    # Reuse index
    return index()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
