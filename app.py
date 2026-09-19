"""Entry point: Flask + diskcache. Без SQLite."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

import diskcache
from flask import Flask, jsonify, render_template, request, Response

from datetime import timedelta
from metrics import build_impacts, pick_recommendation
from timelines import build_windows_timelines, iso_z, parse_dt

import sources


ALGO_VERSION = "0.2.0"
CALC_TTL = 7 * 24 * 3600  # хранить расчёты неделю


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.update(
        CACHE_DIR=os.path.join(app.root_path, "cache"),
        CALC_DIR=os.path.join(app.instance_path, "calcs"),
        JSON_SORT_KEYS=False,
    )
    os.makedirs(app.config["CACHE_DIR"], exist_ok=True)
    os.makedirs(app.config["CALC_DIR"], exist_ok=True)

    # единый путь для requests_cache
    sources.CACHE_DIR = type(sources.CACHE_DIR)(app.config["CACHE_DIR"])

    # storage для расчётов
    app.calcs = diskcache.Cache(app.config["CALC_DIR"])

    register_routes(app)
    return app


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def register_routes(app: Flask) -> None:

    # --- pages ---

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/sources")
    def sources_page():
        return render_template("sources.html")

    # --- health ---

    @app.get("/api/health")
    def health():
        return jsonify({
            "status": "ok",
            "algorithm_version": ALGO_VERSION,
            "time_utc": _iso_z(_now()),
        })

    # --- analyze ---
    @app.post("/api/analyze")
    def analyze():
        body = request.get_json(silent=True) or {}
        mode = (body.get("mode") or "current").lower()
        try:
            start = parse_dt(body.get("start")) if body.get("start") else _now()
            duration_h = max(1, min(8, int(float(body.get("duration", 6)))))
            search_h = max(duration_h, min(24, int(float(body.get("search_period", 24)))))
        except (ValueError, TypeError) as e:
            return jsonify({"error": f"Некорректные параметры: {e}"}), 400

        cutoff = _now() if mode == "historical" else None
        end = start + timedelta(hours=search_h)

        data = sources.collect_for_window(
            start, end,
            cutoff=cutoff,
            include_historical_tle=(mode == "historical"),
            include_cdm=True,
            include_sep=True,
            include_meteors=True,
        )

        impacts = build_impacts(data, start, end)

        # окна-кандидаты
        windows = []
        idx = 0
        w = start
        last_start = start + timedelta(hours=search_h - duration_h)
        while w <= last_start:
            idx += 1
            windows.append({
                "id": idx,
                "start": iso_z(w),
                "end": iso_z(w + timedelta(hours=duration_h)),
                "duration_minutes": duration_h * 60,
            })
            w += timedelta(minutes=30)
        if len(windows) > 8:
            stride = len(windows) // 8
            windows = windows[::stride][:8]

        built = build_windows_timelines(windows, [i.to_dict() for i in impacts])
        recommendation = pick_recommendation(built)

        result = {
            "calculation_id": uuid.uuid4().hex[:12],
            "created_at": _iso_z(_now()),
            "algorithm_version": ALGO_VERSION,
            "mode": mode,
            "start": iso_z(start),
            "duration_hours": duration_h,
            "windows": built,
            "recommendation": recommendation,
            "impacts": [i.to_dict() for i in impacts],
            "sources": [
                {k: env.get(k) for k in ("source", "source_url", "fetched_at", "version", "error")}
                | {"items_count": len(env.get("items") or [])}
                for k, env in data.items()
                if isinstance(env, dict) and "source" in env
            ],
            "errors": data.get("errors", {}),
        }
        app.calcs.set(result["calculation_id"], result, expire=CALC_TTL)
        return jsonify(result)

    # --- get / list / export ---

    @app.get("/api/analyze/<calc_id>")
    def analyze_get(calc_id: str):
        row = app.calcs.get(calc_id)
        if not row:
            return jsonify({"error": "Расчёт не найден"}), 404
        return jsonify(row)

    @app.get("/api/calculations")
    def calculations_list():
        # diskcache не умеет range-запросы, но легко перебрать ключи
        items = []
        for k in app.calcs.iterkeys():
            v = app.calcs.get(k)
            if isinstance(v, dict):
                items.append({
                    "id": k,
                    "created_at": v.get("created_at"),
                    "mode": v.get("mode"),
                    "start": v.get("start"),
                    "duration_hours": v.get("duration_hours"),
                })
        items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        return jsonify({"items": items[:20]})

    @app.get("/api/export/<calc_id>.json")
    def export_json(calc_id: str):
        row = app.calcs.get(calc_id)
        if not row:
            return jsonify({"error": "Расчёт не найден"}), 404
        return Response(
            json.dumps(row, ensure_ascii=False, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": f"attachment; filename=calc_{calc_id}.json"},
        )

    @app.get("/api/export/<calc_id>.csv")
    def export_csv(calc_id: str):
        row = app.calcs.get(calc_id)
        if not row:
            return jsonify({"error": "Расчёт не найден"}), 404

        import csv, io
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["window_id", "start", "end", "bad_min", "warn_min", "threats"])
        for win in row.get("windows", []):
            threats = (win.get("threats_critical") or []) + (win.get("threats_minor") or [])
            w.writerow([
                win.get("id"), win.get("start"), win.get("end"),
                win.get("minutes_bad"), win.get("minutes_warn"),
                "; ".join(threats),
            ])
        return Response(
            buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=calc_{calc_id}.csv"},
        )

    # --- sources / refresh ---

    @app.get("/api/sources")
    def sources_state():
        # просто отдаём перечень зарегистрированных источников + TTL
        return jsonify({"items": sources.list_registered_sources()})

    @app.post("/api/refresh")
    def refresh():
        sources.invalidate_cache()
        return jsonify({"status": "refreshed", "time_utc": _iso_z(_now())})


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)