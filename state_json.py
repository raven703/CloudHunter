# -*- coding: utf-8 -*-
"""
JSON-СТРУКТУРА СОСТОЯНИЯ ДЛЯ ИСПОЛНИТЕЛЬНОГО БОТА И НЕЙРОСЕТИ
=============================================================
Собирает JSON-выгрузку из ParsedUIState (результат EveParserNew.parse).

Схема payload (schema_version: 1) — ключи верхнего уровня:
  meta          — версия схемы, время, размер окна, адрес ui_root, fps;
  system        — инфо-панель (система, секюрити, маршрут и т.д.);
  ship          — HP (null = нет данных), скорость, флаги движения;
  modules       — статусы слотов, репки, списки high/medium/low slots;
  drones        — ангар/космос/максимум, статусы, записи дронов;
  targets       — захваченные цели с экранными координатами + активная цель;
  npc           — классифицированные NPC из овервью (координаты иконок);
  local_chat    — пилоты локала со статусами, threat: SAFE|NEUTRAL|HOSTILE;
  overview      — записи овервью;
  selected_item — выбранное окно объекта с кнопками-действиями;
  scanner       — результаты проб-сканера;
  context_menu  — открытое контекстное меню и пункты;
  asteroid_belts— пояса из открытого подменю;
  neocom        — кнопки неокома и часы;
  executor      — ГОТОВОЕ РЕШЕНИЕ для исполнительного бота:
                  combat_mode, threat_level (0 SAFE | 1 NEUTRAL | 2 HOSTILE),
                  recommended_action (EMERGENCY_EXIT | ACTIVATE_REPAIRER |
                  ENGAGE_TARGETS | RECALL_DRONES | IDLE), lock_candidates
                  (до 3 координат для Ctrl+клика, фрегаты вперёд),
                  recall_drones, activate_repairer, reason;
  nn_features   — нормализованный числовой вектор признаков для нейросети
                  (HP 0..1, булевы флаги 0/1, счётчики; -1.0 = нет данных);
  debug_overlay — рамки Debug HUD (rect -> список [x, y, w, h]).

Точки входа:
  build_state_payload(state, draw_list=None) -> dict
  state_payload_json(state, draw_list=None)  -> str
  save_state_payload(state, draw_list=None, path=...) -> str  (state_payload.json)
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = 1

# Файл, в который выгружается состояние для внешних потребителей
# (исполнительный бот / нейросеть читают его из файла).
DEFAULT_PAYLOAD_PATH = "state_payload.json"

# Троттлинг записи на диск (сек): состояние приходит ~30 Гц, файл писать реже.
_MIN_SAVE_INTERVAL = 0.5

# Сентинел "нет данных" для нейросети (HP в доке/при загрузке = None)
_NN_MISSING = -1.0


# ==================== ВНУТРЕННИЕ ХЕЛПЕРЫ ====================

def _pct(v: Optional[float]) -> Optional[float]:
    """Процент 0..100 или None; округление до 0.1."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return round(float(v), 1)
    return None


def _nn_pct(v: Optional[float]) -> float:
    """Процент -> нормализованное 0..1 (None -> _NN_MISSING)."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return round(max(0.0, min(1.0, float(v) / 100.0)), 4)
    return _NN_MISSING


def _b(flag: Any) -> int:
    """bool -> 0/1 для нейросети."""
    return 1 if flag else 0


def _coords(pairs: List[Tuple[float, float]]) -> List[Dict[str, int]]:
    """[(x, y), ...] -> [{'x': int, 'y': int}, ...] (для кликов исполнителя)."""
    out = []
    for p in pairs or []:
        try:
            out.append({"x": int(round(float(p[0]))), "y": int(round(float(p[1])))})
        except (TypeError, ValueError, IndexError):
            continue
    return out


def _movement_state(state) -> str:
    """Строковый статус движения (как в окне отладки)."""
    if state.is_warp:
        return "WARP"
    if state.is_establishing:
        return "ALIGNING"
    if state.is_orbiting:
        return "ORBITING"
    if state.is_docked:
        return "DOCKED"
    if (state.speed or 0) > 0.5:
        return "MOVING"
    return "STATIONARY"


def _threat_level(state) -> int:
    """0 SAFE | 1 NEUTRAL | 2 HOSTILE — единая шкала угрозы для бота и НС."""
    if state.local_has_hostile:
        return 2
    if state.local_has_neutral:
        return 1
    return 0


def _repairer_to_activate(state) -> Optional[str]:
    """Первый выключенный ремонтник (status == 'READY') — его надо включить."""
    for name, status in (state.repairers or {}).items():
        if status == "READY":
            return name
    return None


def _recommended_action(state, threat_level: int) -> Tuple[str, str, List[Dict[str, int]]]:
    """Рекомендация для исполнительного бота (зеркало приоритетов bot.think).

    Возвращает (action, reason, lock_candidates).
    """
    # 1. БЕЗОПАСНОСТЬ — враг/нейтрал в локале
    if threat_level > 0:
        label = "HOSTILE" if threat_level == 2 else "NEUTRAL"
        return "EMERGENCY_EXIT", f"{label} pilot in local", []

    # 2. ВЫЖИВАНИЕ — выключенная репка
    rep = _repairer_to_activate(state)
    if rep:
        return "ACTIVATE_REPAIRER", f"repairer {rep} is offline", []

    # 3. БОЙ — есть NPC: сначала фрегаты, потом остальные, до 3 целей
    if state.npc_all:
        candidates = (state.npc_frigate + state.npc_all)[:3]
        return "ENGAGE_TARGETS", f"{len(state.npc_all)} npc on overview", _coords(candidates)

    # 4. Дроны в космосе без целей — собрать
    if state.drones_in_space > 0:
        return "RECALL_DRONES", f"{state.drones_in_space} drones in space, no targets", []

    return "IDLE", "no threats and no targets", []


# ==================== СБОРКА PAYLOAD ====================

def build_state_payload(state, draw_list: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Полная JSON-совместимая структура состояния игры.

    :param state:      ParsedUIState (результат EveParserNew.parse)
    :param draw_list:  прямоугольники Debug HUD (опционально)
    :return: dict, пригодный для json.dump
    """
    ts = getattr(state, 'timestamp', 0) or time.time()
    threat = _threat_level(state)
    action, reason, lock_candidates = _recommended_action(state, threat)

    # --- отладочная графика: кортежи -> списки ---
    overlay: List[Dict[str, Any]] = []
    for item in draw_list or []:
        try:
            rect = list(item.get('rect', (0, 0, 0, 0)))
        except TypeError:
            rect = [0, 0, 0, 0]
        overlay.append({
            'rect': rect,
            'color': item.get('color', '#888888'),
            'label': item.get('label', ''),
            'priority': item.get('priority', 0),
        })

    payload: Dict[str, Any] = {
        # ===== МЕТАДАННЫЕ =====
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "timestamp": round(float(ts), 3),
            "datetime": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
            "window": {
                "width": state.window_width,
                "height": state.window_height,
            },
            "ui_root_address": f"0x{state.ui_root_address:X}",
            "is_loading": bool(state.is_loading),
            "fps": state.fps,
        },

        # ===== СИСТЕМА / ЛОКАЦИЯ =====
        "system": {
            "current_system": state.current_system,
            "security_status": state.security_status,
            "nearest_location": state.nearest_location,
            "sovereignty_holder": state.sovereignty_holder,
            "route_destination": state.route_destination,
            "bounty_risk": state.bounty_risk,
        },

        # ===== КОРАБЛЬ =====
        "ship": {
            "hp": {
                "shield_pct": _pct(state.shield_pct),
                "armor_pct": _pct(state.armor_pct),
                "structure_pct": _pct(state.structure_pct),
                "capacitor_pct": _pct(state.capacitor_pct),
            },
            "speed_mps": round(float(state.speed or 0.0), 1),
            "movement": {
                "is_warp": bool(state.is_warp),
                "is_establishing": bool(state.is_establishing),
                "is_orbiting": bool(state.is_orbiting),
                "is_docked": bool(state.is_docked),
                "state": _movement_state(state),
            },
            "is_pvp": bool(state.is_pvp),
        },

        # ===== МОДУЛИ =====
        "modules": {
            "status": dict(state.modules or {}),
            "repairers": dict(state.repairers or {}),
            "high_slots": list(getattr(state, 'high_slots', []) or []),
            "medium_slots": list(getattr(state, 'medium_slots', []) or []),
            "low_slots": list(getattr(state, 'low_slots', []) or []),
        },

        # ===== ДРОНЫ =====
        "drones": {
            "in_bay": state.drones_in_bay,
            "in_space": state.drones_in_space,
            "max": state.drones_max,
            "idle": bool(state.drones_idle),
            "fighting": bool(state.drones_fighting),
            "returning": bool(state.drones_returning),
            "under_attack": bool(state.drones_under_attack),
            "entries": list(state.drone_entries or []),
        },

        # ===== ЗАХВАЧЕННЫЕ ЦЕЛИ =====
        "targets": {
            "count": len(state.targets or []),
            "active": dict(state.active_target) if state.active_target else None,
            "locked": [dict(t) for t in (state.targets or [])],
        },

        # ===== NPC (овервью) =====
        "npc": {
            "count": len(state.npc_all or []),
            "all": _coords(state.npc_all),
            "frigates": _coords(state.npc_frigate),
            "cruisers": _coords(state.npc_cruiser),
            "battlecruisers": _coords(state.npc_battlecruiser),
            "battleships": _coords(state.npc_battleship),
        },

        # ===== ЛОКАЛЬНЫЙ ЧАТ =====
        "local_chat": {
            "player_count": state.local_player_count,
            "has_neutral": bool(state.local_has_neutral),
            "has_hostile": bool(state.local_has_hostile),
            "threat": ("HOSTILE" if threat == 2 else
                       "NEUTRAL" if threat == 1 else "SAFE"),
            "players": [dict(p) for p in (state.local_players or [])],
        },

        # ===== ОВЕРВЬЮ / СКАНЕР / МЕНЮ =====
        "overview": [dict(e) for e in (state.overview_entries or [])],
        "selected_item": dict(state.selected_item) if state.selected_item else None,
        "scanner": [dict(s) for s in (state.scan_results or [])],
        "context_menu": {
            "open": bool(state.menu_open),
            "type": state.menu_type or "",
            "items": [dict(i) for i in (state.menu_items or [])],
        },
        "asteroid_belts": [dict(b) for b in (getattr(state, 'asteroid_belts', None) or [])],
        "neocom": {
            "buttons": [dict(b) for b in (state.neocom_buttons or [])],
            "clock": state.clock_time or "",
        },

        # ===== РЕШЕНИЕ ДЛЯ ИСПОЛНИТЕЛЬНОГО БОТА =====
        "executor": {
            "combat_mode": state.combat_mode_active or "IDLE",
            "threat_level": threat,
            "recommended_action": action,
            "lock_candidates": lock_candidates,
            "recall_drones": action == "RECALL_DRONES",
            "activate_repairer": _repairer_to_activate(state),
            "reason": reason,
        },

        # ===== ВЕКТОР ПРИЗНАКОВ ДЛЯ НЕЙРОСЕТИ =====
        "nn_features": {
            "hp_shield": _nn_pct(state.shield_pct),
            "hp_armor": _nn_pct(state.armor_pct),
            "hp_structure": _nn_pct(state.structure_pct),
            "hp_capacitor": _nn_pct(state.capacitor_pct),
            "speed_mps": round(float(state.speed or 0.0), 1),
            "is_warp": _b(state.is_warp),
            "is_establishing": _b(state.is_establishing),
            "is_orbiting": _b(state.is_orbiting),
            "is_docked": _b(state.is_docked),
            "drones_in_bay": state.drones_in_bay,
            "drones_in_space": state.drones_in_space,
            "drones_max": state.drones_max,
            "drones_idle": _b(state.drones_idle),
            "drones_fighting": _b(state.drones_fighting),
            "drones_returning": _b(state.drones_returning),
            "drones_under_attack": _b(state.drones_under_attack),
            "targets_locked": len(state.targets or []),
            "npc_total": len(state.npc_all or []),
            "npc_frigates": len(state.npc_frigate or []),
            "npc_cruisers": len(state.npc_cruiser or []),
            "npc_battlecruisers": len(state.npc_battlecruiser or []),
            "npc_battleships": len(state.npc_battleship or []),
            "local_players": state.local_player_count,
            "local_has_neutral": _b(state.local_has_neutral),
            "local_has_hostile": _b(state.local_has_hostile),
            "menu_open": _b(state.menu_open),
            "threat_level": threat,
        },

        # ===== DEBUG OVERLAY =====
        "debug_overlay": overlay,
    }

    return payload


def state_payload_json(state, draw_list: Optional[List[Dict[str, Any]]] = None,
                       indent: int = 2) -> str:
    """JSON-строка состояния (ensure_ascii=False — читаемый файл)."""
    return json.dumps(build_state_payload(state, draw_list),
                      ensure_ascii=False, indent=indent)


# ==================== ЗАПИСЬ НА ДИСК ====================

_last_save_ts: float = 0.0


def save_state_payload(state, draw_list: Optional[List[Dict[str, Any]]] = None,
                       path: str = DEFAULT_PAYLOAD_PATH,
                       force: bool = False) -> Optional[str]:
    """Атомарная запись состояния в JSON-файл (с троттлингом).

    Пишет не чаще, чем раз в _MIN_SAVE_INTERVAL секунд (вызывается из
    потока парсинга client._parse_loop).

    :return: путь файла, если запись выполнена; None, если пропущена по троттлингу.
    """
    global _last_save_ts
    now = time.time()
    if not force and (now - _last_save_ts) < _MIN_SAVE_INTERVAL:
        return None

    payload = build_state_payload(state, draw_list)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)   # атомарная замена: читатель не увидит полузаписанный файл
    _last_save_ts = now
    return path
