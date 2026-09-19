"""Entry point: Flask + diskcache + metrics.analyze."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import diskcache
from flask import Flask, jsonify, render_template, request, Response

from metrics import analyze as metrics_analyze, ALGO_VERSION

import sources


CALC_TTL = 7 * 24 * 3600


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.update(
        CACHE_DIR=os.path.join(app.root_path, "cache"),
        CALC_DIR=os.path.join(app.instance_path, "calcs"),
        JSON_SORT_KEYS=False,
    )
    os.makedirs(app.config["CACHE_DIR"], exist_ok=True)
    os.makedirs(app.config["CALC_DIR"], exist_ok=True)

    sources.configure(app.config["CACHE_DIR"])

    app.calcs = diskcache.Cache(app.config["CALC_DIR"])

    register_routes(app)
    return app


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def parse_dt(s) -> datetime:
    """Парсит дату из формы."""
    if s is None:
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

def _now() -> datetime:
    return datetime.now(timezone.utc)


def register_routes(app: Flask) -> None:

    # --- pages ---

    @app.get("/result/<calc_id>")
    def result_page(calc_id: str):
        return render_template("result.html", calc_id=calc_id)

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
    def analyze_endpoint():
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

        # Наличие Space-Track — отключаем CDM, если creds не заданы
        has_st = bool(
            os.getenv("SPACETRACK_USER")
            or os.getenv("SPACETRACK_IDENTITY")
        ) and bool(os.getenv("SPACETRACK_PASSWORD"))

        data = sources.collect_for_window(
            start, end,
            cutoff=cutoff,
            include_historical_tle=(mode == "historical"),
            include_cdm=has_st,
            include_sep=True,
            include_swpc=(mode == "current"),
            include_donki=True,
            include_gfz=True,
            include_rsga=True,
            include_socrates=(mode == "current"),
        )

        result = metrics_analyze(
            data, start, end,
            duration_hours=duration_h,
            cutoff=cutoff,
        )
        payload = result.to_dict()
        payload["calculation_id"] = uuid.uuid4().hex[:12]
        payload["algorithm_version"] = ALGO_VERSION
        app.calcs.set(payload["calculation_id"], payload, expire=CALC_TTL)
        return jsonify(payload)

    # --- get / list / export ---

    @app.get("/api/iss-track/<calc_id>")
    def iss_track(calc_id: str):
        row = app.calcs.get(calc_id)
        if not row:
            return jsonify({"error": "Расчёт не найден"}), 404

        # TLE: из sources_status.tle или tle_history
        tle_env = row.get("sources_status", {}).get("tle") or \
                row.get("sources_status", {}).get("tle_history")
        if not tle_env:
            return jsonify({"error": "TLE не найден в расчёте"}), 400

        # Достаём TLE из envelope → items[0]
        # sources_status не хранит items. Поэтому пойдём в кэш через sources.
        import sources
        tle_items = None
        try:
            env = sources.fetch_tle_iss()
            tle_items = env.get("items") or []
        except Exception:
            pass

        if not tle_items:
            # fallback: пробуем исторический
            env = sources.fetch_tle_history(
                parse_dt(row["start"]),
                parse_dt(row["end"]),
            )
            tle_items = env.get("items") or []

        if not tle_items:
            return jsonify({"error": "TLE недоступен для расчёта"}), 503

        item = tle_items[0]
        line1 = item.get("line1")
        line2 = item.get("line2")

        if not (line1 and line2):
            return jsonify({"error": "TLE без line1/line2"}), 500

        from orbit import iss_position, iss_track

        start = parse_dt(row["start"])
        end = parse_dt(row["end"])
        rec = row.get("recommendation") or {}
        focus_iso = rec.get("start") or row["start"]
        focus = parse_dt(focus_iso)

        try:
            position = iss_position(line1, line2, focus)
            track = iss_track(line1, line2, start, end, step_seconds=90)
        except Exception as e:
            return jsonify({"error": f"Ошибка расчёта орбиты: {e}"}), 500

        return jsonify({
            "tle_epoch": item.get("epoch_utc"),
            "tle_source": tle_env.get("source"),
            "focus": position,
            "track": track,
            "focus_window": {
                "start": row["start"], "end": row["end"],
            },
        })

    # ---

    @app.get("/api/analyze/<calc_id>")
    def analyze_get(calc_id: str):
        row = app.calcs.get(calc_id)
        if not row:
            return jsonify({"error": "Расчёт не найден"}), 404
        return jsonify(row)

    @app.get("/api/calculations")
    def calculations_list():
        items = []
        for k in app.calcs.iterkeys():
            v = app.calcs.get(k)
            if isinstance(v, dict):
                items.append({
                    "id": k,
                    "created_at": v.get("created_at"),
                    "mode": v.get("mode"),
                    "start": v.get("start"),
                    "duration_hours": (
                        v.get("windows", [{}])[0].get("duration_minutes", 0) // 60
                        if v.get("windows") else None
                    ),
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

        # Шапка метаданных
        w.writerow(["# calculation_id", row.get("calculation_id") or calc_id])
        w.writerow(["# algorithm_version", row.get("algorithm_version")])
        w.writerow(["# mode", row.get("mode")])
        w.writerow(["# cutoff", row.get("cutoff") or "—"])
        w.writerow(["# start", row.get("start")])
        w.writerow(["# end", row.get("end")])

        rec = row.get("recommendation") or {}
        w.writerow(["# recommendation.outcome", rec.get("outcome")])
        w.writerow(["# recommendation.window_id", rec.get("window_id")])
        w.writerow(["# recommendation.reason", rec.get("reason")])
        w.writerow([])

        # Окна
        w.writerow([
            "window_id", "start", "end", "duration_min",
            "peak_level_lo", "peak_level_hi",
            "minutes_at_warning", "confidence",
            "warnings_count",
        ])
        for win in row.get("windows", []):
            w.writerow([
                win.get("id"),
                win.get("start"), win.get("end"),
                win.get("duration_minutes"),
                win.get("peak_level_lo"), win.get("peak_level_hi"),
                win.get("minutes_at_warning"),
                win.get("confidence"),
                len(win.get("warnings") or []),
            ])

        w.writerow([])
        w.writerow(["# lines_global"])
        w.writerow(["name", "mechanism", "contributes", "is_nd",
                    "level_lo", "level_hi", "confidence", "kind", "sources"])
        for line in row.get("lines_global", []):
            w.writerow([
                line.get("name"), line.get("mechanism"),
                line.get("contributes"), line.get("is_nd"),
                line.get("level_lo"), line.get("level_hi"),
                line.get("confidence"), line.get("kind"),
                "; ".join(line.get("sources") or []),
            ])

        return Response(
            buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=calc_{calc_id}.csv"},
        )

    # --- sources / refresh ---

    @app.get("/api/sources")
    def sources_state():
        return jsonify({"items": sources.list_registered_sources()})

    @app.post("/api/refresh")
    def refresh():
        sources.invalidate_cache()
        return jsonify({"status": "refreshed", "time_utc": _iso_z(_now())})



app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)