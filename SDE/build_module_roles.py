# -*- coding: utf-8 -*-
"""Генератор карты typeID -> роль модуля/дрона для бота EVE.

Источник данных: SDE CSV (invTypes.csv, invGroups.csv) из Fuzzwork/GitHub.
Результат: module_roles.json в корне проекта (рядом с bot.py/parser.py).

Использование:
    # 1. Сначала посмотреть, какие группы вообще есть (для сверки имён)
    python build_module_roles.py --list-groups

    # 2. Сгенерировать карту
    python build_module_roles.py --build

    # Пути можно указать явно:
    python build_module_roles.py --build \
        --types invTypes.csv \
        --groups invGroups.csv \
        --out ..\\module_roles.json

Почему так, а не по иконкам:
    Иконки модулей в EVE упакованы в атласы (res:/ui/texture/icons/NN_64_MM.png),
    и разные модули (AB vs MWD, разные мета-уровни) могут делить одну и ту же
    иконку. typeID модуля — стабильный и однозначный идентификатор: он не
    меняется между скинами, патчами и языками клиента. Карта строится один
    раз из SDE и живёт как статический ресурс.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


# ==================== СЛОВАРЬ: группа EVE -> роль бота ====================
#
# Ключи — точные groupName из invGroups.csv (SDE, английский).
# Если скрипт в режиме --list-groups покажет что-то, чего тут нет, —
# добавляем сюда и пересобираем. ВАЖНО: сверяй имена буква в букву.
#
# Роли (строки):
#   propulsion          — AB, MWD, всё что двигает корабль (включая фракционные/abyssal)
#   repair_armor        — локальные репки брони (вкл. Ancillary)
#   repair_shield       — локальные шилд-бустеры (вкл. Ancillary)
#   repair_hull         — репки корпуса
#   repair_remote_armor — дистанционные репки брони
#   repair_remote_shield— дистанционные шилд-бустеры
#   repair_remote_hull  — дистанционные репки корпуса
#   gun_energy          — лазеры Amarr
#   gun_projectile      — пушки Minmatar
#   gun_hybrid          — бластеры/рейлганы Gallente/Caldari
#   gun_precursor       — Triglavian (Entropic Disintegrator)
#   gun_vorton          — EDENCOM (Vorton Projector)
#   launcher_missile    — ракетные/торпедные/круизные лаунчеры (все размеры)
#   launcher_defender   — Defender Missile Launcher
#   launcher_bomb       — Bomb Launcher
#   drone_combat        — боевые дроны
#   drone_mining        — майнинг-дроны
#   drone_logistic      — логистические дроны
#   drone_salvage       — сальваг-дроны
#   drone_ewar          — EWAR-дроны
#   drone_fighter       — файтер-дроны (capital)
#   hardener_armor      — armor hardeners / resist
#   hardener_shield     — shield hardeners / resist
#   hardener_dc         — Damage Control (любой)
#   energy_neut         — Energy Neutralizer
#   energy_nos          — Energy Nosferatu
#   ewar_web            — Stasis Webifier
#   ewar_scram          — Warp Scrambler
#   ewar_disrupt        — Warp Disruptor
#
# Модуль может быть в нескольких группах (например, фракционный MWD —
# отдельная группа). Тогда просто добавь ещё одну строку.
ROLE_BY_GROUP: dict[str, str] = {
    # --- Двигатель ---
    # В SDE нет отдельных групп 'Afterburner'/'Microwarpdrive' — всё в этой.
    "Propulsion Module": "propulsion",

    # --- Локальный ремонт (вкл. Ancillary) ---
    "Armor Repair Unit": "repair_armor",
    "Ancillary Armor Repairer": "repair_armor",
    "Shield Booster": "repair_shield",
    "Ancillary Shield Booster": "repair_shield",
    "Hull Repair Unit": "repair_hull",

    # --- Дистанционный ремонт ---
    "Remote Armor Repairer": "repair_remote_armor",
    "Ancillary Remote Armor Repairer": "repair_remote_armor",
    "Mutadaptive Remote Armor Repairer": "repair_remote_armor",
    "Remote Shield Booster": "repair_remote_shield",
    "Ancillary Remote Shield Booster": "repair_remote_shield",
    "Remote Hull Repairer": "repair_remote_hull",

    # --- Орудия (турели) ---
    "Energy Weapon": "gun_energy",
    "Projectile Weapon": "gun_projectile",
    "Hybrid Weapon": "gun_hybrid",
    "Precursor Weapon": "gun_precursor",   # Triglavian Entropic Disintegrator
    "Vorton Projector": "gun_vorton",       # EDENCOM

    # --- Лаунчеры (все размеры и типы) ---
    "Missile Launcher Light": "launcher_missile",
    "Missile Launcher Heavy": "launcher_missile",
    "Missile Launcher Heavy Assault": "launcher_missile",
    "Missile Launcher Rapid Light": "launcher_missile",
    "Missile Launcher Rapid Heavy": "launcher_missile",
    "Missile Launcher Rapid Torpedo": "launcher_missile",
    "Missile Launcher Rocket": "launcher_missile",
    "Missile Launcher Cruise": "launcher_missile",
    "Missile Launcher Torpedo": "launcher_missile",
    "Missile Launcher XL Cruise": "launcher_missile",
    "Missile Launcher XL Torpedo": "launcher_missile",
    "Missile Launcher Defender": "launcher_defender",
    "Missile Launcher Bomb": "launcher_bomb",

    # --- Дроны ---
    "Combat Drone": "drone_combat",
    "Mining Drone": "drone_mining",
    "Logistic Drone": "drone_logistic",
    "Salvage Drone": "drone_salvage",
    "Electronic Warfare Drone": "drone_ewar",
    "Energy Neutralizer Drone": "drone_neut",
    "Stasis Webifying Drone": "drone_web",

    # --- Файтер-дроны (capital-класс; в SDE — 'Fighter', не 'Fighter Drone') ---
    "Light Fighter": "drone_fighter",
    "Heavy Fighter": "drone_fighter",
    "Support Fighter": "drone_fighter",

    # --- Харднеры / резисты ---
    "Armor Hardener": "hardener_armor",
    "Armor Resistance Shift Hardener": "hardener_armor",  # Reactive
    "Flex Armor Hardener": "hardener_armor",
    "Shield Hardener": "hardener_shield",
    "Flex Shield Hardener": "hardener_shield",
    "Damage Control": "hardener_dc",

    # --- Пассивные буферы (не репки, но полезно знать) ---
    "Armor Plate": "buffer_armor",
    "Shield Extender": "buffer_shield",
    "Shield Resistance Amplifier": "buffer_shield_resist",

    # --- Энергия ---
    "Energy Neutralizer": "energy_neut",
    "Energy Nosferatu": "energy_nos",

    # --- EWAR (в SDE — 'Stasis Web', не 'Stasis Webifier') ---
    "Stasis Web": "ewar_web",
    "Warp Scrambler": "ewar_scram",
    "Warp Disrupt Field Generator": "ewar_disrupt",
    "Warp Core Stabilizer": "ewar_core_stab",

    # --- Прочее (полезно для полноты картины) ---
    "Drone Damage Modules": "drone_dda",
    "Drone Tracking Modules": "drone_tracking",
    "Drone Tracking Enhancer": "drone_tracking",
    "Drone Control Range Module": "drone_range",
    "Drone Navigation Computer": "drone_nav",
    "Missile Guidance Enhancer": "launcher_guidance",
    "Missile Guidance Computer": "launcher_guidance",
    "Shield Boost Amplifier": "repair_shield_amp",
}
# ==================== ЧТЕНИЕ CSV ====================

def _read_groups(path: Path) -> dict[int, str]:
    """{groupID: groupName} из invGroups.csv."""
    result: dict[int, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        # Диагностика: покажем колонки ровно один раз
        if reader.fieldnames is None:
            print(f"[ERROR] {path.name}: пустой файл или нет заголовка")
            return result
        print(f"[INFO] {path.name}: columns = {reader.fieldnames}")
        # Ищем колонки по «хвосту» имени — спасает от BOM и опечаток в регистре
        def find_col(candidates):
            for c in reader.fieldnames or []:
                key = c.lstrip("\ufeff").strip().strip('"')
                if key in candidates:
                    return c
            return None

        col_gid = find_col({"groupID", "groupId", "groupid"})
        col_name = find_col({"groupName", "groupname", "name"})
        if col_gid is None or col_name is None:
            print(f"[ERROR] {path.name}: не найдены колонки "
                  f"groupID/groupName. Есть: {reader.fieldnames}")
            return result

        bad = 0
        for row in reader:
            try:
                gid = int(str(row.get(col_gid) or "").strip().strip('"'))
            except ValueError:
                bad += 1
                if bad <= 3:
                    print(f"[WARN] {path.name}: пропуск строки (groupID не число): {row}")
                continue
            gname = str(row.get(col_name) or "").strip().strip('"')
            result[gid] = gname
        print(f"[INFO] {path.name}: прочитано групп = {len(result)}, "
              f"пропущено строк = {bad}")
    return result


def _read_types(path: Path):
    """Итератор (typeID, groupID, typeName, published) из invTypes.csv."""
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            print(f"[ERROR] {path.name}: пустой файл или нет заголовка")
            return
        print(f"[INFO] {path.name}: columns = {reader.fieldnames}")

        def find_col(candidates):
            for c in reader.fieldnames or []:
                key = c.lstrip("\ufeff").strip().strip('"')
                if key in candidates:
                    return c
            return None

        col_tid = find_col({"typeID", "typeId", "typeid"})
        col_gid = find_col({"groupID", "groupId", "groupid"})
        col_name = find_col({"typeName", "typename", "name"})
        col_pub = find_col({"published", "isPublished"})
        if None in (col_tid, col_gid, col_name):
            print(f"[ERROR] {path.name}: не найдены колонки "
                  f"typeID/groupID/typeName. Есть: {reader.fieldnames}")
            return

        total = 0
        yielded = 0
        bad = 0
        for row in reader:
            total += 1
            try:
                tid = int(str(row.get(col_tid) or "").strip().strip('"'))
                gid = int(str(row.get(col_gid) or "").strip().strip('"'))
            except ValueError:
                bad += 1
                if bad <= 3:
                    print(f"[WARN] {path.name}: пропуск строки: {row}")
                continue
            name = str(row.get(col_name) or "").strip().strip('"')
            pub_raw = str(row.get(col_pub) or "0").strip().strip('"') if col_pub else "1"
            published = pub_raw in ("1", "True", "true")
            yielded += 1
            yield tid, gid, name, published

        print(f"[INFO] {path.name}: всего строк = {total}, "
              f"отдано = {yielded}, пропущено = {bad}")

# ==================== РЕЖИМЫ ====================

def list_groups(types_path: Path, groups_path: Path) -> None:
    """Показать все группы, встречающиеся в invTypes, с примерами типов.

    Помогает сверить имена групп с ROLE_BY_GROUP и найти опечатки/пропуски.
    """
    groups = _read_groups(groups_path)
    counts: dict[int, int] = {}
    examples: dict[int, list[str]] = {}

    for tid, gid, name, published in _read_types(types_path):
        if not published:
            continue
        counts[gid] = counts.get(gid, 0) + 1
        if gid not in examples:
            examples[gid] = []
        if len(examples[gid]) < 3:
            examples[gid].append(name)

    print(f"{'groupID':>7}  {'count':>6}  {'groupName':<40}  examples")
    print("-" * 120)
    for gid in sorted(counts.keys(), key=lambda g: -counts[g]):
        gname = groups.get(gid, "?")
        ex = ", ".join(examples.get(gid, []))
        marker = "  [mapped]" if gname in ROLE_BY_GROUP else ""
        print(f"{gid:>7}  {counts[gid]:>6}  {gname:<40}  {ex}{marker}")

    print()
    print(f"Всего групп в invTypes (published): {len(counts)}")
    mapped_groups = {g for g, r in ROLE_BY_GROUP.items()}
    present_groups = {groups.get(gid, "") for gid in counts}
    missing = mapped_groups - present_groups
    if missing:
        print()
        print("[WARN] В ROLE_BY_GROUP есть группы, которых нет в invTypes:")
        for g in sorted(missing):
            print(f"       - {g!r} (проверь имя или удали из словаря)")


def build(types_path: Path, groups_path: Path, out_path: Path) -> None:
    """Сгенерировать module_roles.json из SDE CSV."""
    groups = _read_groups(groups_path)
    result: dict[str, dict[str, str]] = {}
    skipped_unpublished = 0
    by_role: dict[str, int] = {}

    for tid, gid, name, published in _read_types(types_path):
        if not published:
            skipped_unpublished += 1
            continue
        gname = groups.get(gid)
        if not gname:
            continue
        role = ROLE_BY_GROUP.get(gname)
        if not role:
            continue
        result[str(tid)] = {
            "name": name,
            "role": role,
            "group": gname,
        }
        by_role[role] = by_role.get(role, 0) + 1

    out_path.write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    print(f"[OK] Записано: {out_path}")
    print(f"     Всего записей: {len(result)}")
    print(f"     Пропущено unpublished типов: {skipped_unpublished}")
    print()
    print("Разбивка по ролям:")
    for role, cnt in sorted(by_role.items(), key=lambda x: -x[1]):
        print(f"  {role:<24} {cnt:>5}")


# ==================== CLI ====================

def main() -> int:
    p = argparse.ArgumentParser(description="Генератор module_roles.json из SDE CSV")
    p.add_argument("--types", default="invTypes.csv", help="путь к invTypes.csv")
    p.add_argument("--groups", default="invGroups.csv", help="путь к invGroups.csv")
    p.add_argument("--out", default="../module_roles.json",
                   help="куда писать module_roles.json (по умолчанию — в корень проекта)")
    p.add_argument("--list-groups", action="store_true",
                   help="показать все группы из invTypes (для сверки имён)")
    p.add_argument("--build", action="store_true",
                   help="сгенерировать module_roles.json")
    args = p.parse_args()

    types_path = Path(args.types)
    groups_path = Path(args.groups)

    if not types_path.exists():
        print(f"[ERROR] Не найден файл: {types_path.resolve()}")
        return 1
    if not groups_path.exists():
        print(f"[ERROR] Не найден файл: {groups_path.resolve()}")
        return 1

    if args.list_groups:
        list_groups(types_path, groups_path)
        return 0

    if args.build:
        build(types_path, groups_path, Path(args.out))
        return 0

    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())    