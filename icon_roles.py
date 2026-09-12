# -*- coding: utf-8 -*-
"""Загрузчик карты texturePath -> роль модуля (icon_to_role.json).

Файл генерируется скриптом SDE/build_icon_to_role.py на основе
invTypes.csv + invGroups.csv + eveIcons.csv из EVE SDE.

Ключи в JSON — 'NN_MM_XX' (число_число_число) без префикса пути
и без расширения. Это делает карту нечувствительной к тому, как EVE
отдаёт _texturePath в дампе (полный путь / basename / разный регистр /
разное расширение). Пример:

    {
      "3_64_2":  "propulsion",
      "79_64_9": "propulsion",
      "4_64_9":  "ewar_scram",
      "1_64_11": "repair_armor",
      ...
    }
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_ROLES: Optional[dict[str, str]] = None
_ROLES_PATH = Path(__file__).parent / "icon_to_role.json"


def _icon_key(texture_path: Optional[str]) -> Optional[str]:
    """Нормализует любой texturePath в ключ 'NN_MM_XX'.

    Работает одинаково для:
      'res:/ui/texture/icons/79_64_9.png' -> '79_64_9'
      '79_64_9.png'                       -> '79_64_9'
      'res:/UI/Texture/Icons/4_64_9.PNG'  -> '4_64_9'
    """
    if not texture_path:
        return None
    base = str(texture_path).replace("\\", "/").rsplit("/", 1)[-1]
    if "." in base:
        base = base.rsplit(".", 1)[0]
    base = base.strip().lower()
    return base or None


def _load() -> dict[str, str]:
    """Загружает карту icon_to_role.json (ключи — 'NN_MM_XX')."""
    global _ROLES
    if _ROLES is None:
        if _ROLES_PATH.exists():
            try:
                _ROLES = json.loads(_ROLES_PATH.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"[icon_roles] failed to load {_ROLES_PATH}: {e}")
                _ROLES = {}
        else:
            print(f"[icon_roles] not found: {_ROLES_PATH} "
                  f"(сгенерируй SDE/build_icon_to_role.py)")
            _ROLES = {}
    return _ROLES


def role_for_icon(texture_path: Optional[str]) -> Optional[str]:
    """Роль модуля по texturePath иконки.

    Из пути извлекается ключ 'NN_MM_XX' и ищется в карте.
    Не зависит от префикса пути и от расширения файла.
    """
    key = _icon_key(texture_path)
    if not key:
        return None
    return _load().get(key)


def is_propulsion(texture_path: Optional[str]) -> bool:
    """Двигатель (AB/MWD)?"""
    return role_for_icon(texture_path) == "propulsion"


def is_repairer(texture_path: Optional[str]) -> bool:
    """Любая репка (локальная/дистанционная, armor/shield/hull)?"""
    role = role_for_icon(texture_path)
    return bool(role) and role.startswith("repair")