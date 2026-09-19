"""Smoke-тест: sources -> metrics -> timelines без Flask и UI.

Использование:
    python scripts/smoke.py                     # сегодня, окно 6ч
    python scripts/smoke.py --days 1            # за последние сутки
    python scripts/smoke.py --date 2024-05-10   # историческая дата
    python scripts/smoke.py --no-cdm            # без Space-Track (быстрее)
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

# чтобы импортировать модули из корня проекта
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _hr(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def _section(title: str) -> None:
    print(f"\n--- {title} " + "-" * max(1, 60 - len(title)))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="YYYY-MM-DD (UTC), по умолчанию сегодня")
    p.add_argument("--duration", type=int, default=6, help="часы, 1-8")
    p.add_argument("--search", type=int, default=12, help="период поиска, часов")
    p.add_argument("--no-cdm", action="store_true", help="не дергать Space-Track cdm_public")
    p.add_argument("--no-sep", action="store_true", help="не качать NetCDF GOES")
    p.add_argument("--no-meteors", action="store_true")
    p.add_argument("--no-history", action="store_true",
                   help="не тянуть gp_history даже в историческом режиме")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    duration_h = max(1, min(8, args.duration))
    search_h = max(duration_h, min(24, args.search))

    if args.date:
        base = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        mode = "historical"
    else:
        base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        mode = "current"

    start = base
    end = start + timedelta(hours=search_h)

    print(f"Режим:        {mode}")
    print(f"Начало:       {_iso_z(start)}")
    print(f"Конец поиска: {_iso_z(end)}")
    print(f"Длительность: {duration_h} ч")
    print(f"CACHE_DIR:    {os.getenv('CACHE_DIR', 'cache')}")
    print(f"ST creds:     {'заданы' if os.getenv('SPACETRACK_IDENTITY') else 'НЕ заданы'}")

    # --- импорты после sys.path, чтобы видеть локальные модули ---
    try:
        import sources
        from metrics import build_impacts, pick_recommendation
        from timelines import build_windows_timelines, iso_z, parse_dt
    except ImportError as e:
        print(f"\n[FAIL] Не удалось импортировать модули: {e}")
        traceback.print_exc()
        return 1

    cutoff = start if mode == "historical" else None

    # =====================================================================
    # 1. Источники
    # =====================================================================
    _hr("1. СБОР ДАННЫХ")

    data = sources.collect_for_window(
        start, end,
        cutoff=cutoff,
        include_historical_tle=(mode == "historical" and not args.no_history),
        include_cdm=not args.no_cdm,
        include_sep=not args.no_sep,
        include_meteors=not args.no_meteors,
    )

    for name, env in data.items():
        if name in ("window", "errors"):
            continue
        if not isinstance(env, dict):
            continue
        _section(name)
        print(f"  source:   {env.get('source')}")
        print(f"  url:      {env.get('source_url')}")
        print(f"  fetched:  {env.get('fetched_at')}")
        print(f"  version:  {env.get('version')}")
        print(f"  units:    {env.get('units')}")
        print(f"  items:    {len(env.get('items') or [])}")
        if env.get("error"):
            print(f"  ERROR:    {env['error']}")
        if env.get("limitations"):
            print(f"  limits:   {env['limitations']}")

        # примеры первых записей
        items = env.get("items") or []
        for it in items[:2]:
            print(f"    · {it}")

    errors = data.get("errors") or {}
    if errors:
        _section("Ошибки источников")
        for k, v in errors.items():
            print(f"  {k}: {v}")

    # =====================================================================
    # 2. Факторы (Impact)
    # =====================================================================
    _hr("2. ФАКТОРЫ (IMPACT)")

    try:
        impacts = build_impacts(data, start, end)
    except Exception as e:
        print(f"[FAIL] build_impacts упал: {e}")
        traceback.print_exc()
        return 2

    print(f"Всего факторов: {len(impacts)}\n")
    for i in impacts[:20]:
        print(f"  [{i.level:>4}] {i.start.isoformat()} → {i.end.isoformat()}")
        print(f"         {i.mechanism} · {i.kind} · {i.source}")
        print(f"         value={i.value} {i.unit or ''} · {i.threat_label or ''}")

    if len(impacts) > 20:
        print(f"  ... ещё {len(impacts) - 20}")

    # =====================================================================
    # 3. Окна-кандидаты
    # =====================================================================
    _hr("3. ОКНА-КАНДИДАТЫ")

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

    print(f"Сгенерировано окон: {len(windows)}")

    # =====================================================================
    # 4. Таймлайны
    # =====================================================================
    _hr("4. ТАЙМЛАЙНЫ ОКОН")

    try:
        built = build_windows_timelines(windows, [i.to_dict() for i in impacts])
    except Exception as e:
        print(f"[FAIL] build_windows_timelines упал: {e}")
        traceback.print_exc()
        return 3

    for w in built:
        print(f"\n  Окно #{w['id']}  {w['start']} → {w['end']}")
        print(f"    status:   {w.get('weather_status')}")
        print(f"    bad:      {w.get('minutes_bad', 0)} мин")
        print(f"    warn:     {w.get('minutes_warn', 0)} мин")
        print(f"    segments: {len(w.get('segments') or [])}")
        crit = w.get("threats_critical") or []
        minor = w.get("threats_minor") or []
        if crit:  print(f"    critical: {crit}")
        if minor: print(f"    minor:    {minor}")

    # =====================================================================
    # 5. Рекомендация
    # =====================================================================
    _hr("5. РЕКОМЕНДАЦИЯ")

    try:
        rec = pick_recommendation(built)
    except Exception as e:
        print(f"[FAIL] pick_recommendation упал: {e}")
        traceback.print_exc()
        return 4

    if rec:
        print(f"  Окно #{rec['window_id']}")
        print(f"  {rec['start']} → {rec['end']}")
        print(f"  score: {rec['score']}")
        print(f"  reason: {rec['reason']}")
    else:
        print("  Рекомендации нет (нет окон или недостаточно данных)")

    # =====================================================================
    # Итог
    # =====================================================================
    _hr("ИТОГ")
    ok = bool(impacts) and bool(built)
    print(f"Факторов:  {len(impacts)}")
    print(f"Окон:      {len(built)}")
    print(f"Статус:    {'OK' if ok else 'ПУСТО — проверьте источники'}")
    print(f"Ошибок:    {len(errors)}")
    return 0 if ok else 5


if __name__ == "__main__":
    raise SystemExit(main())