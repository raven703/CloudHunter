# -*- coding: utf-8 -*-
"""Проверка HP/капаситора по дампу UI (eveui).

Использование:
    python check_hp.py                  # проверит все uiroot*.json в папке
    python check_hp.py uiroot_new.json  # конкретный дамп

Печатает сырое _lastValue каждого гейджа и результат parse_hp_gauges.
Годится для live-проверки: сделайте дамп, когда щит/броня просажены,
и посмотрите, меняется ли _lastValue у shieldGauge/armorGauge.
"""
import glob
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from full_parser import parse_hp_gauges, find_first_by_type  # noqa: E402


def _fmt(v):
    return f"{v:6.1f}%" if isinstance(v, (int, float)) else "   n/a"


def _iter(node):
    if isinstance(node, dict):
        yield node
        for ch in node.get("children", []) or []:
            yield from _iter(ch)


def check(fn):
    with open(fn, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"--- {fn} ---")
    ship_ui = find_first_by_type(data, "ShipUI")
    if not ship_ui:
        print("  ShipUI не найден (в доке / нет корабля) — HP в дампе отсутствует")
    else:
        for n in _iter(ship_ui):
            ei = n.get("dictEntriesOfInterest", {}) or {}
            if n.get("pythonObjectTypeName") == "ShipHudSpriteGauge":
                print(f"  raw gauge {ei.get('_name')!r}: _lastValue={ei.get('_lastValue')!r}")
        # сырые данные кольца капаситора: видимые ячейки = разряженная часть
        cap = find_first_by_type(ship_ui, "CapacitorContainer")
        if cap:
            depleted = total = 0
            for n in _iter(cap):
                ei = n.get("dictEntriesOfInterest", {}) or {}
                if ei.get("_name") == "pmark":
                    total += 1
                    a = (ei.get("_color") or {}).get("aPercent", 0) or 0
                    if a > 0:
                        depleted += 1
            print(f"  raw capacitor ring: depleted {depleted} cells "
                  f"(captured {total} pmark, ring=72) -> cap ~= {100 - depleted / 72 * 100:.1f}%")
        else:
            print("  CapacitorContainer не найден")
    hp = parse_hp_gauges(data)
    print(f"  parsed: shield={_fmt(hp['shield'])} armor={_fmt(hp['armor'])} "
          f"structure={_fmt(hp['structure'])} capacitor={_fmt(hp['capacitor'])}")


if __name__ == "__main__":
    files = sys.argv[1:] or sorted(glob.glob("uiroot*.json"))
    for fn in files:
        check(fn)
