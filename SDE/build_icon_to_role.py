# -*- coding: utf-8 -*-
"""Генератор карты texturePath -> роль на основе SDE + eveIcons.csv.

Зачем:
    В дампе UI у каждого модуля есть иконка (_texturePath в узле Icon).
    Иконка — стабильный признак: EVE использует разные иконки для
    propulsion / repair / gun / launcher / drone и т.д. Хардкод вида
    ('3_64_2', '1_64_8') неполный (MWD в 79_64_9.png не попал).

Как работает:
    1. invTypes.csv  -> (typeID, groupID, iconID) для published типов.
    2. invGroups.csv -> groupID -> groupName.
    3. ROLE_BY_GROUP -> groupName -> role (согласован в build_module_roles.py).
    4. eveIcons.csv  -> iconID -> iconFile (texturePath).
    5. Собираем ключ 'NN_MM_XX' из iconFile -> set(roles).
    6. Если у иконки одна роль — пишем её как есть.
       Если несколько ролей, но все в одном семействе — пишем зонтик
       (gun, launcher, repair, drone, ...). Это даёт максимум покрытия:
       бот всё равно работает с семействами (двигатель/репка/пушка).
       Если роли из разных семейств — в JSON не пишем (в отчёт).

Ключ в JSON — 'NN_MM_XX' без префикса пути и без расширения:
    res:/ui/texture/icons/79_64_9.png  ->  "79_64_9": "propulsion"
    res:/UI/Texture/Icons/4_64_9.PNG   ->  "4_64_9":  "ewar_scram"
Это делает карту нечувствительной к префиксу пути, регистру и расширению.

Использование:
    python build_icon_to_role.py
    python build_icon_to_role.py --out ..\\icon_to_role.json --icons eveIcons.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

# Переиспользуем словарь ролей из build_module_roles.py
try:
    from build_module_roles import ROLE_BY_GROUP
except ImportError:
    print("[ERROR] build_module_roles.py должен лежать рядом")
    sys.exit(1)


# ==================== ЗОНТИЧНЫЕ РОЛИ ====================
# Если все роли одной иконки принадлежат одному семейству — пишем зонтик.
# Бот работает с семействами (двигатель/репка/пушка/лаунчер/дрон),
# подроли ему не нужны — важно отличить propulsion от repair от gun.
UMBRELLA: dict[str, str] = {
    # пушки
    "gun_energy": "gun",
    "gun_hybrid": "gun",
    "gun_projectile": "gun",
    "gun_precursor": "gun",
    "gun_vorton": "gun",

    # лаунчеры
    "launcher_missile": "launcher",
    "launcher_defender": "launcher",
    "launcher_bomb": "launcher",
    "launcher_guidance": "launcher_guidance",   # guidance — отдельная роль

    # репки (локальные и дистанционные, все виды)
    "repair_armor": "repair",
    "repair_shield": "repair",
    "repair_hull": "repair",
    "repair_remote_armor": "repair",
    "repair_remote_shield": "repair",
    "repair_remote_hull": "repair",
    "repair_shield_amp": "repair",

    # дроны
    "drone_combat": "drone",
    "drone_mining": "drone",
    "drone_logistic": "drone",
    "drone_salvage": "drone",
    "drone_ewar": "drone",
    "drone_neut": "drone",
    "drone_web": "drone",
    "drone_fighter": "drone",

    # модули дронов (DDA, трекинг, навигация)
    "drone_dda": "drone_module",
    "drone_tracking": "drone_module",
    "drone_range": "drone_module",
    "drone_nav": "drone_module",

    # харднеры (резисты)
    "hardener_armor": "hardener",
    "hardener_shield": "hardener",
    "hardener_dc": "hardener",

    # EWAR
    "ewar_web": "ewar",
    "ewar_scram": "ewar",
    "ewar_disrupt": "ewar",
    "ewar_core_stab": "ewar",

    # энергия
    "energy_neut": "energy",
    "energy_nos": "energy",

    # пассивные буферы
    "buffer_armor": "buffer",
    "buffer_shield": "buffer",
    "buffer_shield_resist": "buffer",

    # двигатель уже одна роль
    "propulsion": "propulsion",
}


def _umbrella_of(roles: set[str]) -> str | None:
    """Общий зонтик для набора ролей, если все в одном семействе."""
    umbrellas = {UMBRELLA.get(r, r) for r in roles}
    if len(umbrellas) == 1:
        return next(iter(umbrellas))
    return None


def _icon_key(icon_file: str) -> str | None:
    """Нормализует iconFile в ключ 'NN_MM_XX' (без пути и расширения).

    Примеры:
      'res:/ui/texture/icons/79_64_9.png' -> '79_64_9'
      'res:/UI/Texture/Icons/4_64_9.PNG'  -> '4_64_9'
      '79_64_9.png'                       -> '79_64_9'
      'res:/UI/Texture/Icons/73_16_50.png'-> '73_16_50'
    """
    if not icon_file:
        return None
    base = str(icon_file).replace("\\", "/").rsplit("/", 1)[-1]
    if "." in base:
        base = base.rsplit(".", 1)[0]
    base = base.strip().lower()
    return base or None


# ==================== ЧТЕНИЕ CSV ====================

def _find_col(fieldnames, candidates):
    """Ищет колонку по имени, игнорируя BOM, кавычки и регистр."""
    for c in fieldnames or []:
        k = c.lstrip("\ufeff").strip().strip('"')
        if k in candidates:
            return c
    return None


def _read_groups(path: Path) -> dict[int, str]:
    """{groupID: groupName} из invGroups.csv."""
    result: dict[int, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        col_gid = _find_col(reader.fieldnames, {"groupID", "groupId", "groupid"})
        col_name = _find_col(reader.fieldnames, {"groupName", "groupname"})
        if not col_gid or not col_name:
            print(f"[ERROR] {path.name}: нет колонок groupID/groupName")
            return result
        for row in reader:
            try:
                gid = int(str(row.get(col_gid) or "").strip().strip('"'))
            except ValueError:
                continue
            result[gid] = str(row.get(col_name) or "").strip().strip('"')
    return result


def _read_icons_csv(path: Path) -> dict[int, str]:
    """{iconID: iconFile} из eveIcons.csv."""
    result: dict[int, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        col_id = _find_col(reader.fieldnames, {"iconID", "iconId", "iconid"})
        col_file = _find_col(reader.fieldnames, {"iconFile", "iconfile"})
        if not col_id or not col_file:
            print(f"[ERROR] {path.name}: нет колонок iconID/iconFile. "
                  f"Есть: {reader.fieldnames}")
            return result
        for row in reader:
            try:
                iid = int(str(row.get(col_id) or "").strip().strip('"'))
            except ValueError:
                continue
            fname = str(row.get(col_file) or "").strip().strip('"')
            if fname:
                result[iid] = fname
    return result


def _read_types_with_icons(path: Path):
    """Итератор (typeID, groupID, typeName, iconID) — только published."""
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        col_tid = _find_col(reader.fieldnames, {"typeID", "typeId"})
        col_gid = _find_col(reader.fieldnames, {"groupID", "groupId"})
        col_name = _find_col(reader.fieldnames, {"typeName", "typename"})
        col_icon = _find_col(reader.fieldnames, {"iconID", "iconId", "iconid"})
        col_pub = _find_col(reader.fieldnames, {"published"})
        if not (col_tid and col_gid and col_icon):
            print(f"[ERROR] {path.name}: не хватает колонок")
            return

        for row in reader:
            try:
                tid = int(str(row.get(col_tid) or "").strip().strip('"'))
                gid = int(str(row.get(col_gid) or "").strip().strip('"'))
            except ValueError:
                continue
            pub_raw = str(row.get(col_pub) or "0").strip().strip('"') if col_pub else "1"
            if pub_raw not in ("1", "True", "true"):
                continue
            icon_raw = str(row.get(col_icon) or "").strip().strip('"')
            try:
                icon_id = int(icon_raw) if icon_raw else 0
            except ValueError:
                icon_id = 0
            name = str(row.get(col_name) or "").strip().strip('"')
            yield tid, gid, name, icon_id


# ==================== СБОРКА ====================

def build(types_path: Path, groups_path: Path, icons_path: Path, out_path: Path) -> int:
    groups = _read_groups(groups_path)
    icons = _read_icons_csv(icons_path)
    print(f"[INFO] групп: {len(groups)}, иконок: {len(icons)}")

    icon_roles: dict[str, set[str]] = defaultdict(set)
    icon_examples: dict[str, list[str]] = defaultdict(list)
    missing_icons = 0
    unknown_groups = 0

    for tid, gid, name, icon_id in _read_types_with_icons(types_path):
        if icon_id <= 0:
            continue
        group_name = groups.get(gid)
        if not group_name:
            continue
        role = ROLE_BY_GROUP.get(group_name)
        if not role:
            unknown_groups += 1
            continue
        icon_file = icons.get(icon_id)
        if not icon_file:
            missing_icons += 1
            continue
        key = _icon_key(icon_file)
        if not key:
            continue
        icon_roles[key].add(role)
        if len(icon_examples[key]) < 5:
            icon_examples[key].append(f"{tid}:{name}")

    clean: dict[str, str] = {}     # 'NN_MM_XX' -> роль или зонтик
    conflicts: dict[str, set[str]] = {}
    for icon_key, roles in icon_roles.items():
        if len(roles) == 1:
            clean[icon_key] = next(iter(roles))
        else:
            umbrella = _umbrella_of(roles)
            if umbrella:
                clean[icon_key] = umbrella
            else:
                conflicts[icon_key] = roles

    out_path.write_text(
        json.dumps(clean, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print(f"[OK] Записано: {out_path}")
    print(f"     Иконок в карте (1 роль или зонтик): {len(clean)}")
    print(f"     Реально конфликтных (разные семейства): {len(conflicts)}")
    print(f"     Типов без iconID в eveIcons.csv: {missing_icons}")
    print(f"     Типов вне ROLE_BY_GROUP (не наши — норм): {unknown_groups}")
    print()

    role_count: dict[str, int] = defaultdict(int)
    for role in clean.values():
        role_count[role] += 1
    print("Иконки по ролям/зонтикам:")
    for role, cnt in sorted(role_count.items(), key=lambda x: -x[1]):
        print(f"  {role:<24} {cnt:>4}")

    print()
    print("Проверка AB/MWD (должны быть propulsion):")
    for probe in ("3_64_2", "79_64_9", "4_64_9", "1_64_8"):
        if probe in clean:
            print(f"  {clean[probe]:<24} {probe}")
        else:
            print(f"  {'(нет в clean)':<24} {probe}")

    print()
    print("Проверка лаунчера (12_64_12 — должен быть launcher):")
    for probe in ("12_64_12",):
        if probe in clean:
            print(f"  {clean[probe]:<24} {probe}")
        else:
            print(f"  {'(нет в clean)':<24} {probe}")

    print()
    print("Проверка репок (частые иконки брони/щита/структуры):")
    for probe in ("1_64_11", "2_64_11", "3_64_11", "4_64_11"):
        if probe in clean:
            print(f"  {clean[probe]:<24} {probe}")
        else:
            print(f"  {'(нет в clean)':<24} {probe}")

    if conflicts:
        print()
        print("Реально конфликтные иконки (разные семейства, не пишутся в JSON):")
        for icon_key, roles in sorted(conflicts.items(),
                                       key=lambda x: -len(x[1]))[:30]:
            ex = icon_examples.get(icon_key, [])[:2]
            print(f"  {sorted(roles)}  {icon_key}")
            for e in ex:
                print(f"      e.g. {e}")

    return 0


# ==================== CLI ====================

def main() -> int:
    p = argparse.ArgumentParser(description="iconFile -> role из SDE + eveIcons.csv")
    p.add_argument("--types", default="invTypes.csv")
    p.add_argument("--groups", default="invGroups.csv")
    p.add_argument("--icons", default="eveIcons.csv")
    p.add_argument("--out", default="../icon_to_role.json")
    args = p.parse_args()

    for name, path in (("invTypes", args.types),
                       ("invGroups", args.groups),
                       ("eveIcons", args.icons)):
        if not Path(path).exists():
            print(f"[ERROR] Не найден {name}: {Path(path).resolve()}")
            return 1

    return build(Path(args.types), Path(args.groups),
                 Path(args.icons), Path(args.out))


if __name__ == "__main__":
    sys.exit(main())