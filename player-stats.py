#!/usr/bin/env python3
"""Статистика активности игроков Wanderlust — кто сколько играл и когда был.

Данные берутся из того, что Minecraft пишет сам, без модов:
  world/stats/<uuid>.json  — счётчики, в т.ч. minecraft:play_time (в тиках)
  world/playerdata/<uuid>.dat — время последнего изменения = последний выход
  usercache.json — сопоставление uuid -> ник

Как пользоваться:
  1. Скачать с сервера папку world/ (или запустить скрипт прямо на сервере).
  2. python player-stats.py <путь к папке сервера>

Пример:
  python player-stats.py C:\\Users\\rusla\\Desktop\\wanderlust-server
"""
import json
import os
import sys
from datetime import datetime, timezone

TICKS_PER_HOUR = 20 * 60 * 60


def load_names(server_dir):
    """uuid -> ник из usercache.json."""
    path = os.path.join(server_dir, "usercache.json")
    names = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for entry in json.load(fh):
                names[entry["uuid"].lower()] = entry["name"]
    except Exception as e:
        print(f"[!] usercache.json не прочитан ({e}) — будут только UUID\n")
    return names


def collect(server_dir):
    world = os.path.join(server_dir, "world")
    stats_dir = os.path.join(world, "stats")
    data_dir = os.path.join(world, "playerdata")
    names = load_names(server_dir)

    if not os.path.isdir(stats_dir):
        sys.exit(f"Не найдена папка {stats_dir} — укажите корень сервера с папкой world/")

    rows = []
    for fname in os.listdir(stats_dir):
        if not fname.endswith(".json"):
            continue
        uuid = fname[:-5]
        try:
            with open(os.path.join(stats_dir, fname), encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue

        custom = data.get("stats", {}).get("minecraft:custom", {})
        hours = custom.get("minecraft:play_time", 0) / TICKS_PER_HOUR

        # Последний выход = время изменения файла профиля игрока.
        last_seen = None
        dat = os.path.join(data_dir, uuid + ".dat")
        if os.path.exists(dat):
            last_seen = datetime.fromtimestamp(os.path.getmtime(dat), tz=timezone.utc)

        rows.append({
            "nick": names.get(uuid.lower(), uuid[:8] + "…"),
            "hours": hours,
            "last_seen": last_seen,
            "deaths": custom.get("minecraft:deaths", 0),
        })
    return rows


def main():
    server_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    rows = collect(server_dir)
    if not rows:
        sys.exit("Статистика пуста — на сервер ещё никто не заходил?")

    now = datetime.now(timezone.utc)
    # Сортируем по давности захода: сверху те, кого давно не было.
    rows.sort(key=lambda r: (r["last_seen"] or datetime.min.replace(tzinfo=timezone.utc)))

    print(f"{'ник':<18}{'часов':>8}{'смертей':>9}{'не заходил':>14}")
    print("-" * 51)
    for r in rows:
        if r["last_seen"]:
            days = (now - r["last_seen"]).days
            ago = "сегодня" if days == 0 else f"{days} дн."
        else:
            ago = "нет данных"
        print(f"{r['nick']:<18}{r['hours']:>8.1f}{r['deaths']:>9}{ago:>14}")

    print("-" * 51)
    print(f"всего игроков: {len(rows)}")

    stale = [r for r in rows if r["last_seen"] and (now - r["last_seen"]).days >= 14]
    if stale:
        print(f"\nНе заходили 14+ дней ({len(stale)}): " + ", ".join(r["nick"] for r in stale))

    idle = [r for r in rows if r["hours"] < 1]
    if idle:
        print(f"Наиграли меньше часа ({len(idle)}): " + ", ".join(r["nick"] for r in idle))


if __name__ == "__main__":
    main()
