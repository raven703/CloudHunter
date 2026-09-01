"""
НОВЫЙ ПАРСЕР UI EVE ONLINE
==========================
Специализация: точный разбор локального чата (пилоты и их статусы).

Интеграция в систему:
  - Работает ПАРАЛЛЕЛЬНО со старым EveParser (parser.py) на тех же JSON-данных.
    (см. client.py: reader_loop -> _parse_loop)
  - Результат старого парсера по-прежнему уходит боту (bot.py не меняется).
  - Результат ЭТОГО парсера уходит в окно отладки (main.py -> ParserDebugWindow).

Структура локального чата в JSON:
  ChatWindowStack -> XmppChatWindow -> Container(content) -> Container(main)
    -> Container(с Divider 'user_list_divider') -> BasicDynamicScroll(userlist)
    -> ... -> XmppChatSimpleUserEntry

Пилот БЕЗ флажка (FlagIconWithState) — это сам игрок (например, Nikky Naari) — игнорируется.

Статусы пилотов определяются по _hint флажка:
  "Neutral" / "No Standing"      -> NEUTRAL
  "Terrible" / "Bad" / "Hostile" -> HOSTILE
  "Alliance"                     -> ALLIANCE
  "Excellent" / "Good"           -> GOOD
  "Corp" / "Corporation"         -> CORPORATION
"""
from __future__ import annotations

import io
import re
import time
from contextlib import redirect_stdout
from typing import Any, Dict, List, Optional, Tuple

from ParsedUIState import ParsedUIState

# Полный разбор всего UI (HP, дроны, овервью и т.д.) переиспользуем из полного парсера,
# а локальный чат разбираем СВОИМ (более точным) алгоритмом — см. parse_local_chat_new.
from full_parser import parse_ui_root

STANDING_NEUTRAL = "NEUTRAL"
STANDING_HOSTILE = "HOSTILE"
STANDING_ALLIANCE = "ALLIANCE"
STANDING_GOOD = "GOOD"
STANDING_CORPORATION = "CORPORATION"
STANDING_UNKNOWN = "UNKNOWN"

STANDING_COLORS = {
    STANDING_NEUTRAL: "#ffdd55",
    STANDING_HOSTILE: "#ff5555",
    STANDING_ALLIANCE: "#55ff88",
    STANDING_GOOD: "#55ddff",
    STANDING_CORPORATION: "#88aaff",
    STANDING_UNKNOWN: "#888888",
}

# Слова, которые точно не являются именами пилотов (заголовки каналов и пр.)
_BAD_NAME_WORDS = ("local", "channel", "chat", "online", "away", "busy",
                   "do not disturb", "fleet", "wing", "member", "guest",
                   "character", "standing")


# ==================== БАЗОВЫЕ ХЕЛПЕРЫ ====================

def get_val(val: Any) -> int:
    """Безопасное получение значения из dict с int_low32"""
    if isinstance(val, dict):
        return val.get("int_low32", 0)
    return val if isinstance(val, (int, float)) else 0


def clean_html(text: Any) -> str:
    """Очистка HTML тегов"""
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', ' ', str(text))
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _is_plausible_name(text: Any) -> bool:
    """Похоже ли значение на имя пилота"""
    if not isinstance(text, str):
        return False
    t = text.strip()
    if len(t) < 3 or len(t) > 40:
        return False
    low = t.lower()
    if any(word in low for word in _BAD_NAME_WORDS):
        return False
    if t.isdigit():
        return False
    return True


# ==================== ПОИСК ПО ДЕРЕВУ JSON ====================

def iter_nodes(node):
    """Генератор: все узлы дерева (dict) в порядке обхода."""
    if isinstance(node, dict):
        yield node
        for child in node.get('children', []) or []:
            yield from iter_nodes(child)


def find_first_by_type(root, obj_type):
    """Первый узел заданного типа (pythonObjectTypeName)."""
    for node in iter_nodes(root):
        if node.get('pythonObjectTypeName') == obj_type:
            return node
    return None


def find_all_by_type(root, obj_type):
    """Все узлы заданного типа."""
    return [n for n in iter_nodes(root) if n.get('pythonObjectTypeName') == obj_type]


def _find_flag_icon(node):
    """Первый FlagIconWithState в поддереве узла (или None)."""
    for n in iter_nodes(node):
        if n.get('pythonObjectTypeName') == 'FlagIconWithState':
            return n
    return None


def _looks_like_pilot_name(text: Any) -> bool:
    """Более строгая проверка имени пилота, чем _is_plausible_name.

    Требует хотя бы одну заглавную букву или цифру — отсекает служебные
    имена UI-узлов вида 'flag', 'content', 'scroll' и т.п.
    """
    if not _is_plausible_name(text):
        return False
    return any(ch.isupper() for ch in str(text)) or any(ch.isdigit() for ch in str(text))


def _extract_pilot_name(entry: Dict) -> Optional[str]:
    """Извлечение имени пилота из узла XmppChatSimpleUserEntry.

    Приоритет:
      1) собственные _text / _setText / _name узла;
      2) тексты дочерних Label-узлов (_text/_setText, затем _name);
      3) любые иные тексты в поддереве.
    """
    ei = entry.get('dictEntriesOfInterest') or {}

    # 1. Собственные поля узла
    for key in ('_text', '_setText', '_name'):
        val = ei.get(key)
        if isinstance(val, str) and _is_plausible_name(val):
            return clean_html(val)

    # 2. Тексты в поддереве (label-узлы имеют _text/_setText)
    label_texts = []
    other_texts = []

    for node in iter_nodes(entry):
        if node is entry:
            continue
        ntype = node.get('pythonObjectTypeName', '')
        nei = node.get('dictEntriesOfInterest') or {}

        for key in ('_text', '_setText'):
            val = nei.get(key)
            if isinstance(val, str) and val.strip():
                (label_texts if 'Label' in ntype else other_texts).append(clean_html(val))

        val = nei.get('_name')
        if isinstance(val, str) and 'Label' in ntype and val.strip():
            label_texts.append(clean_html(val))

    for t in label_texts:
        if _is_plausible_name(t):
            return t
    for t in other_texts:
        if _looks_like_pilot_name(t):
            return t

    # 3. Последний шанс: собственный _name без строгой проверки
    name = ei.get('_name')
    if isinstance(name, str) and name.strip():
        return clean_html(name)

    return None

# === SECTION: CHAT PARSING ===

def _standing_from_hint(hint: Any) -> Tuple[str, bool]:
    """Статус пилота по _hint флажка FlagIconWithState.

    Возвращает (standing, is_alliance).
    """
    h = clean_html(hint).lower()

    if any(k in h for k in ('neutral', 'no standing')):
        return STANDING_NEUTRAL, False
    if any(k in h for k in ('terrible', 'bad', 'hostile')):
        return STANDING_HOSTILE, False
    if 'alliance' in h:
        return STANDING_ALLIANCE, True
    if any(k in h for k in ('excellent', 'good')):
        return STANDING_GOOD, False
    if any(k in h for k in ('corp', 'corporation')):
        return STANDING_CORPORATION, False
    return STANDING_UNKNOWN, False


def parse_chat_user_new(user_node: Dict, parent_x: int = 0, parent_y: int = 0) -> Optional[Dict[str, Any]]:
    """Парсинг ОДНОГО пилота локального чата (узла XmppChatSimpleUserEntry).

    - Имя пилота — из узла или его Label-детей (_extract_pilot_name).
    - Статус — по _hint флажка FlagIconWithState (_standing_from_hint).
    - Пилот БЕЗ флажка — это сам игрок (например, Nikky Naari) → игнорируем (None).

    Возвращает {"name", "standing", "alliance", "x", "y"} или None.
    """
    name = _extract_pilot_name(user_node)
    if not name:
        return None

    flag = _find_flag_icon(user_node)
    if flag is None:
        # Нет флажка — это сам игрок, а не пилот в локале.
        return None

    hint = (flag.get('dictEntriesOfInterest') or {}).get('_hint', '')
    standing, alliance = _standing_from_hint(hint)

    ei = user_node.get('dictEntriesOfInterest') or {}
    x = parent_x + get_val(ei.get('_displayX'))
    y = parent_y + get_val(ei.get('_displayY'))

    return {
        'name': name,
        'standing': standing,
        'alliance': alliance,
        'x': x,
        'y': y,
    }


def _collect_entries(node, px: int, py: int, acc: List[Tuple[Dict, int, int]]):
    """Рекурсивный сбор XmppChatSimpleUserEntry с накопленными координатами."""
    if not isinstance(node, dict):
        return
    ei = node.get('dictEntriesOfInterest') or {}
    ax = px + get_val(ei.get('_displayX'))
    ay = py + get_val(ei.get('_displayY'))

    if node.get('pythonObjectTypeName') == 'XmppChatSimpleUserEntry':
        acc.append((node, ax, ay))

    for child in node.get('children', []) or []:
        _collect_entries(child, ax, ay, acc)


def _find_local_chat_window(root: Dict) -> Optional[Dict]:
    """Поиск окна ЛОКАЛЬНОГО чата (XmppChatWindow).

    Стратегия:
      1) XmppChatWindow, где в наборе _text/_setText встречается 'local';
      2) иначе — первое XmppChatWindow, под которым есть XmppChatSimpleUserEntry;
      3) на худой конец — первый XmppChatWindow.
    """
    windows = find_all_by_type(root, 'XmppChatWindow')
    if not windows:
        return None

    for w in windows:
        for node in iter_nodes(w):
            ei = node.get('dictEntriesOfInterest') or {}
            for key in ('_text', '_setText'):
                val = ei.get(key)
                if isinstance(val, str) and val.strip().lower().startswith('local'):
                    return w

    for w in windows:
        if find_first_by_type(w, 'XmppChatSimpleUserEntry'):
            return w

    return windows[0]

def parse_local_chat_new(root: Dict) -> Dict[str, Any]:
    """ТОЧНЫЙ парсинг локального чата.

    Структура (см. докмодуль):
      ChatWindowStack -> XmppChatWindow -> Container(content) -> Container(main)
        -> Container(с user_list_divider) -> BasicDynamicScroll(userlist)
        -> ... -> XmppChatSimpleUserEntry

    Собираем ВСЕ XmppChatSimpleUserEntry в окне локального чата,
    для каждого вызываем parse_chat_user_new
    (пилот без флажка — сам игрок — отбрасывается автоматически).

    Возвращает {'player_count', 'has_neutral', 'has_hostile', 'players'}.
    """
    result = {
        'player_count': 0,
        'has_neutral': False,
        'has_hostile': False,
        'players': [],
    }

    window = _find_local_chat_window(root)
    if window is None:
        return result

    entries = []
    _collect_entries(window, 0, 0, entries)

    players = []
    seen = set()
    for entry, ex, ey in entries:
        parsed = parse_chat_user_new(entry, ex, ey)
        if not parsed:
            continue
        # Дедупликация по имени: скролл может рендерить запись повторно
        name = parsed['name']
        if name in seen:
            continue
        seen.add(name)
        players.append(parsed)

    result['players'] = players
    result['player_count'] = len(players)
    result['has_neutral'] = any(p['standing'] == STANDING_NEUTRAL for p in players)
    result['has_hostile'] = any(p['standing'] == STANDING_HOSTILE for p in players)

    return result


# ==================== КЛАСС НОВОГО ПАРСЕРА ====================

class EveParserNew:
    """Новый парсер UI.

    - Весь остальной UI (HP, дроны, овервью, ...) — через full_parser.parse_ui_root.
    - Локальный чат — через parse_local_chat_new (точный алгоритм).

    parse() возвращает (ParsedUIState, draw_list) — как старый EveParser,
    но с корректным списком пилотов в state.local_players.
    """

    def __init__(self):
        self.last_draw_list: List[Dict[str, Any]] = []

    def parse(self, json_data: Dict) -> Tuple[ParsedUIState, List[Dict[str, Any]]]:
        state = self._parse_full(json_data)

        # Локальный чат — наш точный алгоритм
        chat = parse_local_chat_new(json_data)
        state.local_player_count = chat['player_count']
        state.local_has_neutral = chat['has_neutral']
        state.local_has_hostile = chat['has_hostile']
        state.local_players = chat['players']

        # Дополнительная нормализация полей (репки, боевой режим) для отладки
        self._finalize_state(state)

        draw_list = self._build_draw_list(state)
        self.last_draw_list = draw_list

        return state, draw_list

    def _finalize_state(self, state: ParsedUIState) -> None:
        """До-расчёт полей, которые full_parser не заполняет:
        - repairers: модули-ремонтники по иконке (как в старом парсере);
        - combat_mode_active: эвристика по текущей обстановке.
        """
        # 1. Репки — определяем по иконке модуля (репер иконки как в parser.py)
        rep_icon = '1_64_11'
        repairers: Dict[str, str] = {}
        for slot_list in (state.high_slots, state.medium_slots, state.low_slots):
            for m in slot_list or []:
                icon = str(m.get('icon') or '')
                if rep_icon in icon.lower() or 'repair' in icon.lower():
                    slot_name = m.get('name', '')
                    if slot_name:
                        repairers[slot_name] = 'ACTIVE' if m.get('active') else 'READY'
        state.repairers = repairers

        # 2. Боевой режим (отображается в окне отладки, боту не передаётся)
        if state.local_has_hostile or state.local_has_neutral:
            state.combat_mode_active = "SAFETY"
        elif state.drones_fighting or state.drones_in_space > 0:
            state.combat_mode_active = "DRONES"
        elif state.targets or state.npc_all:
            state.combat_mode_active = "GUNS"
        else:
            state.combat_mode_active = "IDLE"

    def _parse_full(self, json_data: Dict) -> ParsedUIState:
        """Полный разбор остального UI. Debug-вывод full_parser подавляется."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                return parse_ui_root(json_data)
            except Exception as e:
                print(f"[NewParser] full_parser error: {e}")
                return ParsedUIState()

    @staticmethod
    def _build_draw_list(state: ParsedUIState) -> List[Dict[str, Any]]:
        """Визуализация пилотов локального чата (цвет по статусу)."""
        draw_list = []
        for p in state.local_players or []:
            standing = p.get('standing') or STANDING_UNKNOWN
            color = STANDING_COLORS.get(standing, STANDING_COLORS[STANDING_UNKNOWN])
            draw_list.append({
                'rect': (p.get('x', 0), p.get('y', 0), 18, 16),
                'color': color,
                'label': p.get('name', ''),
                'priority': 12,
            })
        return draw_list
# ==================== ОТЧЁТ ДЛЯ ОКНА ОТЛАДКИ ====================

def _pct_tag(pct: float) -> str:
    """Цветовой тег по значению процента."""
    if pct is None: return 'dim'
    pct = float(pct)
    if pct <= 25.0: return 'danger'
    if pct <= 50.0: return 'warn'
    return 'good'


def _fmt_pct(pct: Optional[float], width: int = 5) -> str:
    """Форматирование процента: None -> 'n/a' (нет данных в дампе)."""
    if pct is None:
        return 'n/a'.rjust(width)
    return f"{float(pct):{width}.1f}"


def _bar(pct: float, width: int = 18) -> str:
    """Псевдографическая полоса процента (моноширинная)."""
    pct = max(0.0, min(100.0, float(pct or 0)))
    filled = int(round(width * pct / 100.0))
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def _short_icon(icon: Any) -> str:
    """Короткое имя иконки из полного texturePath."""
    icon = str(icon or '')
    return icon.rsplit('/', 1)[-1] if icon else '-'


def _fmt_dist(d: Any) -> str:
    """Человекочитаемое расстояние (м/км/AU)."""
    if d is None: return "-"
    try:
        d = float(d)
    except (TypeError, ValueError):
        return "-"
    if d >= 149597870700:
        return f"{d / 149597870700:.2f} AU"
    if d >= 1000:
        return f"{d / 1000:.1f} km"
    return f"{d:.0f} m"


def _fmt_dist_simple(d: Any) -> str:
    """Короткое число расстояния без единиц (для колонок)."""
    if d is None: return "-"
    try:
        d = float(d)
    except (TypeError, ValueError):
        return "-"
    if d >= 149597870700:
        return f"{d / 149597870700:.2f}"
    if d >= 1000:
        return f"{d / 1000:.1f}"
    return f"{d:.0f}"


def _pilot_tag(standing: str) -> str:
    """Tk-тег для статуса пилота (переиспользуем цвета standing)."""
    return standing if standing in STANDING_COLORS else STANDING_UNKNOWN


def _movement_state(state: ParsedUIState) -> str:
    """Строковый статус движения."""
    if state.is_warp:
        return "WARP"
    if state.is_establishing:
        return "ALIGNING"
    if state.is_orbiting:
        return "ORBITING"
    if (state.speed or 0) > 0.5:
        return "MOVING"
    return "STATIONARY"


def build_debug_blocks(state: ParsedUIState) -> List[Tuple[str, str]]:
    """Структурированный цветной отчёт состояния нового парсера.

    Возвращает список кортежей (tag, text), где tag — имя цвета-тега
    (title/header/good/warn/danger/info/dim) для Tk Text (main.py).
    """
    blocks: List[Tuple[str, str]] = []

    def add(tag, text):
        blocks.append((tag, text))

    def header(text):
        add('header', text)

    def dim(text):
        add('dim', text)

    ts = time.strftime('%H:%M:%S', time.localtime(getattr(state, 'timestamp', 0) or 0))

    # ===== ЗАГОЛОВОК =====
    add('title', "EVE NEW PARSER DEBUG")
    dim(f"{'=' * 74}")
    dim(f"updated: {ts}  window: {state.window_width}x{state.window_height}  fps: {state.fps if state.fps else '-'}")

    # ===== 1. КОРАБЛЬ =====
    header("== SHIP (HP / SPEED) ==")
    add(_pct_tag(state.shield_pct), f"  shield    {_bar(state.shield_pct)} {_fmt_pct(state.shield_pct)}%")
    add(_pct_tag(state.armor_pct), f"  armor     {_bar(state.armor_pct)} {_fmt_pct(state.armor_pct)}%")
    add(_pct_tag(state.structure_pct), f"  structure {_bar(state.structure_pct)} {_fmt_pct(state.structure_pct)}%")
    add(_pct_tag(state.capacitor_pct), f"  capacitor {_bar(state.capacitor_pct)} {_fmt_pct(state.capacitor_pct)}%")
    add('info', f"  speed     {state.speed:8.1f} m/s")
    mov = _movement_state(state)
    mov_tag = 'good' if mov in ('ORBITING', 'STATIONARY', 'MOVING') else ('warn' if mov == 'ALIGNING' else 'danger')
    add(mov_tag, f"  state     {mov}")
    dim(f"  flags     warp={state.is_warp} establish={state.is_establishing} orbit={state.is_orbiting} docked={state.is_docked}")

    # ===== 2. МОДУЛИ =====
    header("== MODULES ==")
    # high_slots/medium_slots/low_slots добавляются динамически в full_parser,
    # поэтому берём их через getattr (на пустом state их может не быть).
    for label, slot_list in (("HIGH", getattr(state, 'high_slots', [])),
                             ("MEDIUM", getattr(state, 'medium_slots', [])),
                             ("LOW", getattr(state, 'low_slots', []))):
        add('header', f"  -- {label} SLOTS --")
        if not slot_list:
            dim("     (none)")
            continue
        for m in slot_list or []:
            name = str(m.get('name') or '').replace('InFlight', '').replace('Slot', ' ')
            active = bool(m.get('active'))
            icon = _short_icon(m.get('icon'))
            tag = 'good' if active else 'dim'
            add(tag, f"     {name:<14} {'ACTIVE' if active else 'READY':<7} {icon}")
    repairers = getattr(state, 'repairers', {}) or {}
    if repairers:
        add('header', "  -- REPAIRERS --")
        for name, status in repairers.items():
            short = str(name).replace('InFlight', '').replace('Slot', ' ')
            add('good' if status == 'ACTIVE' else 'warn', f"     {short:<14} {status}")
    active_mods = [n for n, s in (state.modules or {}).items() if s == 'ACTIVE']
    if active_mods:
        add('info', f"  ACTIVE MODULES: {', '.join(sorted(active_mods))}")
# ===== 3. ДРОНЫ =====
    header("== DRONES ==")
    add('info', f"  bay: {state.drones_in_bay}   space: {state.drones_in_space}/{state.drones_max}")
    if state.drones_returning:
        d_tag, d_status = 'warn', 'RETURNING'
    elif state.drones_fighting:
        d_tag, d_status = 'warn', 'FIGHTING'
    elif state.drones_idle:
        d_tag, d_status = 'good', 'IDLE'
    elif state.drones_in_space == 0 and state.drones_in_bay > 0:
        d_tag, d_status = 'info', 'IN BAY'
    else:
        d_tag, d_status = 'dim', 'UNKNOWN'
    add(d_tag, f"  status: {d_status}")
    add('danger' if state.drones_under_attack else 'dim',
        f"  under_attack: {state.drones_under_attack}")
    entries = state.drone_entries or []
    if entries:
        add('header', "  -- DRONES IN SPACE --")
        for d in entries:
            d_hp = float(d.get('hp', 100))
            name = str(d.get('name') or '?')
            st = str(d.get('status') or 'UNKNOWN')
            st_tag = 'good' if st == 'IDLE' else ('warn' if st == 'FIGHTING' else 'info')
            hp_tag = _pct_tag(d_hp)
            add(st_tag, f"     {name:<24} {st:<9} {_bar(d_hp, 14)} {d_hp:.0f}%")

    # ===== 4. ЦЕЛИ =====
    header("== TARGETS ==")
    targets = state.targets or []
    if not targets:
        dim("  (no targets)")
    for i, t in enumerate(targets):
        name = str(t.get('name') or f"target_{i}")
        dist = _fmt_dist_simple(t.get('distance'))
        is_active = bool(t.get('is_active'))
        tag = 'danger' if is_active else 'info'
        star = " *" if is_active else ""
        add(tag, f"  {i+1:>2}. {name:<22} d={dist:>9}  ({t.get('x', 0):>4},{t.get('y', 0):>4}){star}")
    if state.active_target:
        add('danger', f"  ACTIVE: {state.active_target.get('name', '?')}")

    # ===== 5. ВЫБРАННЫЙ ОБЪЕКТ =====
    header("== SELECTED ITEM ==")
    sel = state.selected_item or {}
    sel_actions = sel.get('actions') or []
    if sel and (sel.get('name') or sel.get('distance') or sel_actions):
        add('info', f"  name: {sel.get('name') or '-'}")
        add('info', f"  distance: {_fmt_dist(sel.get('distance'))}")
        add('danger' if sel.get('is_target') else 'dim',
            f"  is_target: {'YES' if sel.get('is_target') else 'no'}")
        dim(f"  icon: {_short_icon(sel.get('icon'))}")
        dim(f"  window @ ({sel.get('x', 0)},{sel.get('y', 0)}) "
            f"{sel.get('w', 0)}x{sel.get('h', 0)}")
        # Действия, которые можно применить к выбранному объекту (кнопки окна)
        if sel_actions:
            add('header', "  -- ACTIONS --")
            for a in sel_actions:
                label = str(a.get('action') or a.get('name') or '?')
                add('good', f"     [{label:<16}] @ ({a.get('x', 0):>4},{a.get('y', 0):>4})"
                            f"  {_short_icon(a.get('icon'))}")
        else:
            dim("  (no actions)")
    else:
        dim("  (no selected item)")

    # ===== 6. ОВЕРВЬЮ =====
    header("== OVERVIEW ==")
    ov = state.overview_entries or []
    if not ov:
        dim("  (empty)")
    add('info', f"  {len(ov)} entries")
    for e in ov[:80]:
        name = str(e.get('name') or '?')
        dist = _fmt_dist_simple(e.get('distance'))
        etype = str(e.get('type') or '')
        e_tag = 'danger' if 'hostile' in etype.lower() else 'info'
        add(e_tag, f"     {name:<30} d={dist:>9}  type={etype:<12} ({e.get('x', 0)},{e.get('y', 0)})")
    if len(ov) > 80:
        dim(f"     ... {len(ov) - 80} more")

    # ===== 7. NPC =====
    header("== NPC ==")
    add('info', f"  total: {len(state.npc_all or [])}   "
                f"fr={len(state.npc_frigate or [])} cr={len(state.npc_cruiser or [])} "
                f"bcr={len(state.npc_battlecruiser or [])} bs={len(state.npc_battleship or [])}")
    for label, lst in (("frigates   ", state.npc_frigate),
                       ("cruisers   ", state.npc_cruiser),
                       ("battcruisers", state.npc_battlecruiser),
                       ("battleships", state.npc_battleship)):
        for (nx, ny) in lst or []:
            add('warn', f"     {label} @ ({nx:>4},{ny:>4})")
# ===== 8. ЛОКАЛЬНЫЙ ЧАТ =====
    header(f"== LOCAL CHAT ==")
    add('warn' if state.local_has_neutral else 'dim',
        f"  neutral in local: {state.local_has_neutral}")
    add('danger' if state.local_has_hostile else 'dim',
        f"  hostile in local: {state.local_has_hostile}")
    players = state.local_players or []
    if players:
        add('header', "  -- PILOTS --")
        for p in players:
            st = str(p.get('standing') or STANDING_UNKNOWN)
            alliance = " [ALLIANCE]" if p.get('alliance') else ""
            add(_pilot_tag(st), f"     [{st:<11}] {p.get('name', '?')}{alliance}")
    else:
        dim("  (no pilots)")

    # ===== 9. ПРОБ-СКАНЕР =====
    header("== PROBE SCANNER ==")
    scans = state.scan_results or []
    if not scans:
        dim("  (no scan results)")
    add('info', f"  {len(scans)} results")
    for s in scans[:30]:
        add('info', f"     {str(s.get('id') or '-'):<12} "
                    f"{str(s.get('name') or '?'):<28} "
                    f"{str(s.get('group') or ''):<20} "
                    f"d={_fmt_dist(s.get('distance')):>10}  "
                    f"sig={float(s.get('signal') or 0):5.1f}%")
    if len(scans) > 30:
        dim(f"     ... {len(scans) - 30} more")

    # ===== 10. КОНТЕКСТНОЕ МЕНЮ =====
    header("== CONTEXT MENU ==")
    if state.menu_open:
        add('info', f"  open: {state.menu_type or 'context_menu'}")
        for item in (state.menu_items or []):
            if item.get('type') == 'entry':
                add('info', f"     [{str(item.get('text') or '?'):<32} @ ({item.get('x', 0)},{item.get('y', 0)})]")
            else:
                dim("     ----------")
    else:
        dim("  (closed)")

    # ===== 11. ASTEROID BELTS (из контекстного меню) =====
    header("== ASTEROID BELTS ==")
    belts = getattr(state, 'asteroid_belts', None) or []
    if belts:
        add('info', f"  {len(belts)} belts (from open context menu)")
        for b in belts[:40]:
            add('info', f"     {str(b.get('name') or '?'):<40} "
                        f"@ ({b.get('x', 0)},{b.get('y', 0)})")
        if len(belts) > 40:
            dim(f"     ... {len(belts) - 40} more")
    else:
        dim("  (none — belt submenu not open)")

    # ===== 12. СТАТУС БОТА =====
    header("== BOT STATUS ==")
    mode = str(state.combat_mode_active or "IDLE")
    mode_tag = 'danger' if mode == 'SAFETY' else ('good' if mode == 'IDLE' else 'warn')
    add(mode_tag, f"  combat mode: {mode}")
    dim(f"  is_loading: {state.is_loading}   is_pvp: {state.is_pvp}")

    # ===== 13. ТЕХНИЧЕСКАЯ ИНФОРМАЦИЯ =====
    header("== TECH ==")
    dim(f"  window: {state.window_width} x {state.window_height}")
    dim(f"  ui_root_address: 0x{state.ui_root_address:X}")
    dim(f"  updated: {ts}")

    return blocks

