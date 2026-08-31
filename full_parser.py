from __future__ import annotations  
from ParsedUIState import ParsedUIState
import json
import re
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def get_val(val: Any) -> int:
    """Безопасное получение значения из dict с int_low32"""
    if isinstance(val, dict):
        return val.get("int_low32", 0)
    return val if isinstance(val, (int, float)) else 0


def get_str(val: Any) -> str:
    """Безопасное получение строки"""
    if val is None:
        return ""
    if isinstance(val, dict):
        return str(val.get("text", val.get("_text", "")))
    return str(val)


def clean_html(text: str) -> str:
    """Очистка HTML тегов"""
    if not text:
        return ""
    # Удаляем HTML теги
    text = re.sub(r'<[^>]+>', ' ', str(text))
    # Удаляем лишние пробелы
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def parse_distance(text: str) -> float:
    """Парсинг расстояния из текста"""
    text = clean_html(text).replace(',', '.').lower()
    # Убираем пробелы-разделители тысяч: '1 234 m' -> '1234 m'
    text = re.sub(r'(\d)\s+(?=\d)', r'\1', text)
    
    # Ищем число с единицей измерения
    patterns = [
        (r'([\d.]+)\s*au', 149597870700),  # AU в метрах
        (r'([\d.]+)\s*km', 1000),
        (r'([\d.]+)\s*m', 1),
    ]
    
    for pattern, multiplier in patterns:
        match = re.search(pattern, text)
        if match:
            return float(match.group(1)) * multiplier
    return 0.0


# Строгий шаблон строки-расстояния: '0 m', '1 234 m', '12,5 km', '2.3 AU'
_DISTANCE_LABEL_RE = re.compile(r'^[\d\s.,]+\s*(au|km|m)$', re.IGNORECASE)


def _is_distance_label(text: str) -> bool:
    """Строгое определение строки-расстояния (в отличие от хрупкой 'm' in text)."""
    return bool(_DISTANCE_LABEL_RE.match(text.strip()))


def parse_percent(text: str) -> float:
    """Парсинг процентов"""
    text = clean_html(text).replace(',', '.')
    match = re.search(r'([\d.]+)\s*%', text)
    if match:
        return float(match.group(1))
    return 0.0


def find_children_by_type(node: Dict, obj_type: str) -> List[Dict]:
    """Рекурсивный поиск детей по типу"""
    results = []
    if node.get('pythonObjectTypeName') == obj_type:
        results.append(node)
    
    for child in node.get('children', []):
        results.extend(find_children_by_type(child, obj_type))
    
    return results


def find_first_by_type(node: Dict, obj_type: str) -> Optional[Dict]:
    """Поиск первого ребенка с указанным типом (рекурсивно)"""
    if not isinstance(node, dict):
        return None
    
    if node.get('pythonObjectTypeName') == obj_type:
        return node
    
    for child in node.get('children', []):
        result = find_first_by_type(child, obj_type)
        if result:
            return result
    
    return None

def get_node_coords(node: Dict, parent_x: int = 0, parent_y: int = 0) -> Dict[str, int]:
    """Получение координат узла"""
    ei = node.get('dictEntriesOfInterest', {})
    return {
        'x': get_val(ei.get('_displayX', 0)) + parent_x,
        'y': get_val(ei.get('_displayY', 0)) + parent_y,
        'w': get_val(ei.get('_displayWidth', 0)),
        'h': get_val(ei.get('_displayHeight', 0))
    }


def get_node_text(node: Dict) -> str:
    """Получение текста из узла"""
    ei = node.get('dictEntriesOfInterest', {})
    for key in ['_setText', '_text', '_hint', '_name']:
        if key in ei:
            return clean_html(str(ei[key]))
    return ""


# ==================== ПАРСЕР КОНТЕКСТНОГО МЕНЮ ====================

def parse_context_menu(root: Dict) -> Dict[str, Any]:
    """Парсинг контекстного меню"""
    result = {
        'open': False,
        'menu_type': '',
        'items': [],
        'submenus': []
    }
    
    # Ищем LayerCore l_menu
    l_menu = None
    for child in root.get('children', []):
        if child.get('dictEntriesOfInterest', {}).get('_name') == 'l_menu':
            l_menu = child
            break
    
    if not l_menu:
        return result
    
    # Ищем ContextMenu или ContextSubMenu
    for child in l_menu.get('children', []):
        obj_type = child.get('pythonObjectTypeName', '')
        if obj_type in ['ContextMenu', 'ContextSubMenu']:
            result['open'] = True
            result['menu_type'] = obj_type
            result['items'] = parse_menu_items(child)
    
    return result


def parse_menu_items(menu_node: Dict) -> List[Dict]:
    """Парсинг пунктов меню"""
    items = []
    
    # Ищем mainCont
    main_cont = None
    for child in menu_node.get('children', []):
        if child.get('pythonObjectTypeName') == 'ContainerAutoSize':
            if child.get('dictEntriesOfInterest', {}).get('_name') == 'mainCont':
                main_cont = child
                break
    
    if not main_cont:
        return items
    
    for child in main_cont.get('children', []):
        if child.get('pythonObjectTypeName') == 'MenuEntryView':
            items.append(parse_menu_entry(child))
        elif child.get('pythonObjectTypeName') == 'Line':
            items.append({'type': 'separator'})
    
    return items


def parse_menu_entry(entry_node: Dict) -> Dict:
    """Парсинг одного пункта меню"""
    ei = entry_node.get('dictEntriesOfInterest', {})
    coords = get_node_coords(entry_node)
    
    result = {
        'type': 'entry',
        'name': str(ei.get('_name', '')),
        'text': '',
        'x': coords['x'],
        'y': coords['y'],
        'w': coords['w'],
        'h': coords['h'],
        'has_submenu': False,
        'icon': None
    }
    
    # Ищем текст
    for child in entry_node.get('children', []):
        if child.get('pythonObjectTypeName') == 'TextBody':
            text = get_node_text(child)
            if text:
                result['text'] = text
        
        if child.get('pythonObjectTypeName') == 'Sprite':
            texture = child.get('dictEntriesOfInterest', {}).get('_texturePath', '')
            if texture:
                if isinstance(texture, dict):
                    result['icon'] = str(texture.get('text', ''))
                else:
                    result['icon'] = str(texture)
    
    # Проверяем наличие подменю (по стрелке справа)
    for child in entry_node.get('children', []):
        if child.get('pythonObjectTypeName') == 'Sprite':
            texture = child.get('dictEntriesOfInterest', {}).get('_texturePath', '')
            if '1_16_14' in str(texture):
                result['has_submenu'] = True
    
    return result


# ==================== ПАРСЕР HP КОРАБЛЯ ====================

def _to_float(val: Any) -> Optional[float]:
    """Безопасное преобразование значения дампа в float (None если не число)."""
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, dict):
        # Вариант упаковки 64-битного числа: {'int': ..., 'int_low32': ...}
        for key in ('float_low32', 'int_low32'):
            low = val.get(key)
            if isinstance(low, (int, float)) and not isinstance(low, bool):
                return float(low)
    return None


def _parse_capacitor_cells(cap_node: Dict) -> Optional[float]:
    """Уровень капаситора (%) по кольцу ячеек внутри CapacitorContainer.

    Кольцо = 18 колонок (Transform 'powerColumn') x 4 ячейки (Sprite 'pmark')
    = 72 ячейки. Заполненная ячейка: _color.aPercent > 0 (обычно 50),
    пустая: _color.aPercent == 0. Спрайт 'powerblink'
    (capacitorCellGlow.png) — мерцающая ячейка на границе заполнения,
    в подсчёте не участвует. Точность — одна ячейка (~1.4%).

    Fallback: если 'pmark' не найдены, пробуем любой дочерний узел с
    _lastValue (на случай изменения структуры дампа в будущих патчах).
    """
    filled = 0
    total = 0
    for col in cap_node.get('children', []) or []:
        if not isinstance(col, dict):
            continue
        for spr in col.get('children', []) or []:
            if not isinstance(spr, dict):
                continue
            ei = spr.get('dictEntriesOfInterest', {}) or {}
            if ei.get('_name') != 'pmark':
                continue
            total += 1
            color = ei.get('_color') or {}
            alpha = _to_float(color.get('aPercent'))
            if alpha is not None and alpha > 0:
                filled += 1
    if total > 0:
        return max(0.0, min(100.0, filled / total * 100.0))

    # Fallback: gauge-узел с _lastValue внутри CapacitorContainer.
    stack = list(cap_node.get('children', []) or [])
    while stack:
        node = stack.pop(0)
        if not isinstance(node, dict):
            continue
        ei = node.get('dictEntriesOfInterest', {}) or {}
        if '_lastValue' in ei:
            raw = _to_float(ei.get('_lastValue'))
            if raw is not None:
                return max(0.0, min(100.0, raw * 100.0))
        stack.extend(node.get('children', []) or [])
    return None


def parse_hp_gauges(root: Dict) -> Dict[str, Optional[float]]:
    """Парсинг HP корабля: shield / armor / structure / capacitor в процентах.

    Для показателя, который прочитать не удалось, возвращается None
    ("нет данных"), а НЕ фейковые 100%:
      * ShipUI -> HudContainer -> CenterHudContainer -> HPGauges не найдены
        (в доке, на экране загрузки и т.п.);
      * у ShipHudSpriteGauge отсутствует атрибут _lastValue.

    _lastValue гейджа — доля заполнения 0..1 (ею клиент рисует полосу HP),
    поэтому shield/armor/structure парсятся именно из него.

    Капаситор: кольцо из 18 колонок (powerColumn) x 4 ячеек (Sprite 'pmark')
    = 72 ячейки внутри CapacitorContainer. Заполненная ячейка имеет
    _color.aPercent > 0 (обычно 50), пустая — aPercent == 0; 'powerblink'
    (capacitorCellGlow.png) — мерцающая ячейка на границе заполнения.
    Процент = заполненные ячейки / все ячейки (шаг ~1.4%).
    См. _parse_capacitor_cells().
    """
    result: Dict[str, Optional[float]] = {
        'shield': None,
        'armor': None,
        'structure': None,
        'capacitor': None,
    }

    # Ищем ShipUI → HPGauges
    ship_ui = find_first_by_type(root, 'ShipUI')
    if not ship_ui:
        return result

    hud_container = find_first_by_type(ship_ui, 'HudContainer')
    if not hud_container:
        return result

    center_hud = find_first_by_type(hud_container, 'CenterHudContainer')
    if not center_hud:
        return result

    hp_gauges = find_first_by_type(center_hud, 'HPGauges')
    if hp_gauges:
        # Парсим каждый gauge
        for child in hp_gauges.get('children', []):
            if child.get('pythonObjectTypeName') != 'ShipHudSpriteGauge':
                continue
            ei = child.get('dictEntriesOfInterest', {}) or {}
            name = str(ei.get('_name', '')).lower()
            raw = _to_float(ei.get('_lastValue'))
            if raw is None:
                continue
            value = max(0.0, min(100.0, raw * 100.0))
            if 'shield' in name:
                result['shield'] = value
            elif 'armor' in name:
                result['armor'] = value
            elif 'structure' in name:
                result['structure'] = value

    # Парсим Capacitor (кольцо из ячеек pmark, см. _parse_capacitor_cells).
    capacitor = find_first_by_type(center_hud, 'CapacitorContainer')
    if capacitor:
        result['capacitor'] = _parse_capacitor_cells(capacitor)

    return result


# ==================== ПАРСЕР СКОРОСТИ ====================

def parse_speed(root: Dict) -> float:
    """Парсинг скорости корабля.

    Реальная структура sppedLabel в JSON:
      ShipUI -> HudContainer -> CenterHudContainer -> SpeedGauge
        -> Container -> Container -> EveLabelSmall(_name='speedLabel', _setText='262 m/s')

    Ищем метку speedLabel РЕКУРСИВНО в поддереве SpeedGauge.
    Поддерживаются: 'm/s', 'km/s', 'AU/s' / 'au/s' (переводится в метры).
    """
    ship_ui = find_first_by_type(root, 'ShipUI')
    if not ship_ui:
        return 0.0

    speed_gauge = find_first_by_type(ship_ui, 'SpeedGauge')
    if not speed_gauge:
        return 0.0

    # Рекурсивно ищем любой узел с _name == 'speedLabel'
    def find_speed_label(node):
        if not isinstance(node, dict):
            return None
        ei = node.get('dictEntriesOfInterest', {}) or {}
        if ei.get('_name') == 'speedLabel':
            return node
        for child in node.get('children', []) or []:
            res = find_speed_label(child)
            if res is not None:
                return res
        return None

    label = find_speed_label(speed_gauge)
    if label is None:
        return 0.0

    ei = label.get('dictEntriesOfInterest', {}) or {}
    for key in ('_setText', '_text'):
        text = clean_html(str(ei.get(key, '')))
        if not text:
            continue
        # m/s
        match = re.search(r'([\d.]+)\s*m/s', text, re.IGNORECASE)
        if match:
            return float(match.group(1))
        # AU/s (в варпе) -> переводим в метры
        match = re.search(r'([\d.]+)\s*a[uд]?\s*/s', text, re.IGNORECASE)
        if match:
            return float(match.group(1)) * 149597870700.0
        # km/s
        match = re.search(r'([\d.]+)\s*km/s', text, re.IGNORECASE)
        if match:
            return float(match.group(1)) * 1000.0

    return 0.0


# ==================== ПАРСЕР МОДУЛЕЙ ====================

def parse_modules(root: Dict) -> Dict[str, Any]:
    """Парсинг модулей"""
    result = {
        'modules': {},
        'high_slots': [],
        'medium_slots': [],
        'low_slots': []
    }
    
    ship_ui = find_first_by_type(root, 'ShipUI')
    if not ship_ui:
        return result
    
    hud_container = find_first_by_type(ship_ui, 'HudContainer')
    if not hud_container:
        return result
    
    slots_container = find_first_by_type(hud_container, 'SlotsContainer')
    if not slots_container:
        return result
    
    for child in slots_container.get('children', []):
        if child.get('pythonObjectTypeName') == 'ShipSlot':
            ei = child.get('dictEntriesOfInterest', {})
            slot_name = str(ei.get('_name', ''))
            is_active = False
            icon = None
            
            # Определяем тип слота
            slot_type = 'unknown'
            if 'High' in slot_name:
                slot_type = 'high'
            elif 'Medium' in slot_name:
                slot_type = 'medium'
            elif 'Low' in slot_name:
                slot_type = 'low'
            
            # Проверяем активность модуля
            for sub in child.get('children', []):
                if sub.get('pythonObjectTypeName') == 'ModuleButton':
                    sub_ei = sub.get('dictEntriesOfInterest', {})
                    if sub_ei.get('ramp_active') == True:
                        is_active = True
                    
                    # Ищем иконку
                    for icon_child in sub.get('children', []):
                        if icon_child.get('pythonObjectTypeName') == 'Icon':
                            icon_data = icon_child.get('dictEntriesOfInterest', {})
                            icon = str(icon_data.get('_texturePath', ''))
            
            module_info = {
                'name': slot_name,
                'active': is_active,
                'icon': icon
            }
            
            result['modules'][slot_name] = 'ACTIVE' if is_active else 'READY'
            
            if slot_type == 'high':
                result['high_slots'].append(module_info)
            elif slot_type == 'medium':
                result['medium_slots'].append(module_info)
            elif slot_type == 'low':
                result['low_slots'].append(module_info)
    
    return result


# ==================== ПАРСЕР ДРОНОВ ====================

def parse_drones(root: Dict) -> Dict[str, Any]:
    """Парсинг окна дронов.

    Реальная структура в JSON:
      заголовки:  DronesWindow -> Container -> Container -> DroneGroupHeaderInBay/InSpace
                    -> ContainerAutoSize -> EveLabelMedium (_setText='Drones in Bay (14)')
      scroll:      DronesWindow -> Container -> Container -> ContainerAutoSize
                    -> Scroll(_name='dronesInSpaceScroll')
      записи:      ... -> Scroll -> Container -> Container -> Container
                    -> DroneInSpaceEntry
    """
    result = {
        'in_bay': 0,
        'in_space': 0,
        'max': 0,
        'idle': False,
        'fighting': False,
        'returning': False,
        'under_attack': False,
        'entries': []
    }

    drones_window = find_first_by_type(root, 'DronesWindow')
    if not drones_window:
        return result

    # --- 1. Заголовки "Drones in Bay (N)" / "Drones in Space (N/M)" ---
    # Рекурсивно ищем любые Label с текстом заголовков
    def walk(node):
        if not isinstance(node, dict):
            return
        ei = node.get('dictEntriesOfInterest', {}) or {}
        txt = clean_html(str(ei.get('_setText', '') or ei.get('_text', '')))
        nums = re.findall(r'\d+', txt)
        if 'Drones in Bay' in txt and nums:
            result['in_bay'] = int(nums[0])
        elif 'Drones in Space' in txt:
            if nums:
                result['in_space'] = int(nums[0])
            if len(nums) > 1:
                result['max'] = int(nums[1])
        # Полоска урона дрона (droneGaugeBarDmg): ширина > 0 => дрон под атакой
        if 'droneGaugeBarDmg' in str(ei.get('_name', '')):
            w = ei.get('_displayWidth', ei.get('_width', 0))
            if isinstance(w, dict):
                w = w.get('int_low32', 0)
            if isinstance(w, (int, float)) and w > 2:
                result['under_attack'] = True

        for child in node.get('children', []) or []:
            walk(child)

    walk(drones_window)

    # --- 2. Записи дронов в космосе ---
    # РЕАЛЬНАЯ структура вложенности:
    #   Scroll(_name='dronesInSpaceScroll') -> Container(maincontainer)
    #     -> Container(__clipper) -> Container(__content) -> DroneInSpaceEntry
    # поэтому ищем записи РЕКУРСИВНО по всему поддереву окна.
    space_entries = []

    def collect_entries(node, acc):
        if not isinstance(node, dict):
            return
        if node.get('pythonObjectTypeName') == 'DroneInSpaceEntry':
            acc.append(node)
        for child in node.get('children', []) or []:
            collect_entries(child, acc)

    collect_entries(drones_window, space_entries)

    # --- 3. Статусы дронов — по записям в космосе (stateSprite._hint) ---
    for entry in space_entries:
        parsed = parse_drone_entry(entry)
        if parsed and (parsed.get('name') or parsed.get('status')):
            result['entries'].append(parsed)
            # Уточняем статусы по записям
            st = parsed.get('status', '')
            if st == 'IDLE':
                result['idle'] = True
            elif st == 'FIGHTING':
                result['fighting'] = True
            elif st == 'RETURNING':
                result['returning'] = True

    # Нет дронов в космосе => активных статусов нет (все в ангаре)
    if not result['entries'] and result['in_space'] == 0:
        result['idle'] = False
        result['fighting'] = False
        result['returning'] = False

    return result


def parse_drone_entry(entry_node: Dict) -> Dict:
    """Парсинг одной записи дрона (DroneInSpaceEntry).

    Реальная структура:
      DroneInSpaceEntry
        -> Container(mainCont) -> Container(textClipper)
             -> TextBody(_name='entryLabel',
                         _setText='Caldari Navy Vespa <color=#FF8DC169>Idle</color>')
        -> Sprite(_name='stateSprite', _hint='Idle')
        -> Container(gaugesParent) -> ... (полосы HP)

    Имя — часть текста ДО <color>..., статус — из <color>...</color> или _hint.
    """
    result = {
        'name': '',
        'status': 'UNKNOWN',
        'hp': 100.0,
        'under_attack': False
    }

    name_candidates = []
    status_candidates = []

    def walk(node, depth=0):
        if not isinstance(node, dict) or depth > 6:
            return
        t = node.get('pythonObjectTypeName', '')
        ei = node.get('dictEntriesOfInterest', {}) or {}

        if t == 'TextBody':
            raw = str(ei.get('_setText', '') or ei.get('_text', '') or '')
            text = clean_html(raw)
            if text:
                name_candidates.append((raw, text))

        if t == 'Sprite':
            hint = str(ei.get('_hint', '') or '')
            if hint:
                status_candidates.append(hint)

        for child in node.get('children', []) or []:
            walk(child, depth + 1)

    walk(entry_node)

    # Статус: из _hint спрайта (приоритет) или из <color>...</color> в тексте.
    # Возможные _hint у stateSprite в EVE: 'Idle', 'Attacking', 'Fighting',
    # 'Engaging', 'Returning', 'Mining', 'Following'...
    def _st_from_text(low):
        if 'idle' in low:
            return 'IDLE'
        if 'returning' in low:
            return 'RETURNING'
        if 'attack' in low or 'fight' in low or 'engag' in low:
            return 'FIGHTING'
        return None

    for hint in status_candidates:
        st = _st_from_text(str(hint).lower())
        if st:
            result['status'] = st

    # Имя: часть текста ДО цветового статуса
    for raw, text in name_candidates:
        # 'Caldari Navy Vespa <color=#FF8DC169>Idle</color>'
        m = re.search(r'<color[^>]*>([^<]+)</color>', raw)
        if m:
            st = _st_from_text(m.group(1).strip().lower())
            if st:
                result['status'] = st
            # имя = текст до <color
            name_part = raw.split('<color')[0].strip()
            name = clean_html(name_part)
            if name:
                result['name'] = name
        elif text and not result['name']:
            # нет цветового тега — имя это весь текст (вырезаем слово-статус)
            result['name'] = re.sub(r'\b(Idle|Fighting|Returning|Attacking|Engaging)\b', '', text, flags=re.I).strip()

    # Если имя так и не нашли, а статус остался в тексте — отрезаем хвост
    if not result['name'] and name_candidates:
        m = re.match(r'^([^<]+?)\s*<color', name_candidates[0][0])
        if m:
            result['name'] = clean_html(m.group(1))
        else:
            result['name'] = name_candidates[0][1]

    return result


# ==================== ПАРСЕР ЦЕЛЕЙ (l_target) ====================

def parse_targets(root: Dict) -> Dict[str, Any]:
    """Парсинг захваченных целей (слой l_target).

    Реальная структура в дампах (uiroot.json):
        l_viewstate -> l_view_overlays -> l_target (LayerCore)
            -> Container -> Container('aRowx_N') -> TargetInBar('target')
    Вложенность слоёв меняется между версиями UI, поэтому l_target и ряды
    aRowx_N НЕ ищем по прямой вложенности: цели собираем рекурсивно по типу
    TargetInBar/TargetInActive с накоплением абсолютных координат
    (так же, как это делает старый парсер) — поиск не зависит от иерархии.
    """
    result = {
        'targets': [],
        'active_target': None
    }

    # Собираем все узлы захваченных целей с абсолютными координатами
    found = []
    _collect_target_nodes(root, 0, 0, found)

    for target_node, ax, ay in found:
        target = parse_target_entry(target_node, ax, ay)
        if target:
            result['targets'].append(target)
            if target.get('is_active') and result['active_target'] is None:
                result['active_target'] = target

    return result


def _collect_target_nodes(node: Dict, px: int, py: int, out: List) -> None:
    """Рекурсивный сбор узлов захваченных целей (TargetInBar/TargetInActive).

    Координаты накапливаются по цепочке _displayX/_displayY — получаем
    абсолютные экранные координаты цели.
    """
    if not isinstance(node, dict):
        return
    ei = node.get('dictEntriesOfInterest', {})
    ax = px + get_val(ei.get('_displayX'))
    ay = py + get_val(ei.get('_displayY'))
    if node.get('pythonObjectTypeName') in ('TargetInBar', 'TargetInActive'):
        out.append((node, ax, ay))
    for child in node.get('children', []) or []:
        _collect_target_nodes(child, ax, ay, out)


def parse_target_entry(target_node: Dict, abs_x: int = 0, abs_y: int = 0) -> Optional[Dict]:
    """Парсинг одной цели из узла TargetInBar.

    Структура цели в дампе:
      TargetInBar('target')
        -> Container('barAndImageCont') -> Transform('iconPar')
             -> Transform -> ActiveTargetIndicator  (индикатор АКТИВНОЙ цели)
        -> ContainerAutoSize('labelContainer')
             -> EveLabelSmall ('4GQ-XQ -', 'Kuvakei [FVHC]', '0 m')
    """
    result = {
        'name': '',
        'distance': 0.0,
        'is_active': False,
        'x': abs_x,
        'y': abs_y
    }

    # Активная цель помечена индикатором где-то в поддереве
    # (TargetInBar -> barAndImageCont -> iconPar -> Transform -> ActiveTargetIndicator),
    # поэтому ищем рекурсивно, а не только среди прямых детей.
    if find_first_by_type(target_node, 'ActiveTargetIndicator'):
        result['is_active'] = True

    # Имя и расстояние — из меток labelContainer
    name_parts = []
    for sub in target_node.get('children', []):
        if sub.get('pythonObjectTypeName') == 'ContainerAutoSize':
            if sub.get('dictEntriesOfInterest', {}).get('_name') == 'labelContainer':
                for label in sub.get('children', []):
                    if label.get('pythonObjectTypeName') == 'EveLabelSmall':
                        text = get_node_text(label)
                        if not text:
                            continue
                        if _is_distance_label(text):
                            result['distance'] = parse_distance(text)
                        else:
                            name_parts.append(text)

    # Имя цели может занимать несколько строк — склеиваем их
    result['name'] = ' '.join(name_parts) if name_parts else 'Target'

    return result


# ==================== ПАРСЕР ВЫБРАННОГО ОБЪЕКТА ====================

def parse_selected_item(root: Dict) -> Optional[Dict]:
    """Парсинг выбранного объекта"""
    selected_wnd = find_first_by_type(root, 'SelectedItemWnd')
    if not selected_wnd:
        return None
    
    result = {
        'name': '',
        'distance': 0.0,
        'is_target': False,
        'icon': ''
    }
    
    for child in selected_wnd.get('children', []):
        if child.get('pythonObjectTypeName') == 'Container':
            if child.get('dictEntriesOfInterest', {}).get('_name') == 'content':
                for sub in child.get('children', []):
                    if sub.get('pythonObjectTypeName') == 'Container':
                        if sub.get('dictEntriesOfInterest', {}).get('_name') == 'main':
                            for cont in sub.get('children', []):
                                if cont.get('pythonObjectTypeName') == 'Container':
                                    if cont.get('dictEntriesOfInterest', {}).get('_name') == 'topCont':
                                        for label in cont.get('children', []):
                                            if label.get('pythonObjectTypeName') == 'EveLabelMedium':
                                                text = get_node_text(label)
                                                if 'm' in text:
                                                    result['distance'] = parse_distance(text)
                                                elif text:
                                                    result['name'] = text
    
    # Проверяем, является ли цель захваченной
    for child in selected_wnd.get('children', []):
        if child.get('pythonObjectTypeName') == 'Container':
            if child.get('dictEntriesOfInterest', {}).get('_name') == 'content':
                for sub in child.get('children', []):
                    if sub.get('pythonObjectTypeName') == 'Container':
                        if sub.get('dictEntriesOfInterest', {}).get('_name') == 'main':
                            for cont in sub.get('children', []):
                                if cont.get('pythonObjectTypeName') == 'ContainerAutoSize':
                                    for button in cont.get('children', []):
                                        if button.get('pythonObjectTypeName') == 'SelectedItemButton':
                                            name = button.get('dictEntriesOfInterest', {}).get('_name', '')
                                            if 'UnLock' in name:
                                                result['is_target'] = True
    
    return result


# ==================== ПАРСЕР ОВЕРВЬЮ ====================

def parse_overview(root: Dict) -> List[Dict]:
    """Парсинг овервью"""
    result = []
    
    overview_wnd = find_first_by_type(root, 'OverviewWindow')
    if not overview_wnd:
        return result
    
    # Ищем scroll с записями
    for child in overview_wnd.get('children', []):
        if child.get('pythonObjectTypeName') == 'Container':
            if child.get('dictEntriesOfInterest', {}).get('_name') == 'content':
                for sub in child.get('children', []):
                    if sub.get('pythonObjectTypeName') == 'Container':
                        if sub.get('dictEntriesOfInterest', {}).get('_name') == 'main':
                            for scroll in sub.get('children', []):
                                if scroll.get('pythonObjectTypeName') == 'BasicDynamicScroll':
                                    # Ищем записи
                                    for entry in scroll.get('children', []):
                                        if entry.get('pythonObjectTypeName') == 'Container':
                                            for clipper in entry.get('children', []):
                                                if clipper.get('pythonObjectTypeName') == 'Container':
                                                    for content in clipper.get('children', []):
                                                        if content.get('pythonObjectTypeName') == 'Container':
                                                            for scroll_entry in content.get('children', []):
                                                                if scroll_entry.get('pythonObjectTypeName') == 'OverviewScrollEntry':
                                                                    parsed = parse_overview_entry(scroll_entry)
                                                                    if parsed:
                                                                        result.append(parsed)
    
    return result


def parse_overview_entry(entry_node: Dict) -> Optional[Dict]:
    """Парсинг одной записи овервью"""
    result = {
        'name': '',
        'distance': 0.0,
        'type': '',
        'x': 0,
        'y': 0,
        'icon': ''
    }
    
    for child in entry_node.get('children', []):
        if child.get('pythonObjectTypeName') == 'OverviewLabel':
            ei = child.get('dictEntriesOfInterest', {})
            text = clean_html(str(ei.get('_text', '')))
            
            if 'km' in text or 'm' in text:
                result['distance'] = parse_distance(text)
            elif text:
                result['name'] = text
        
        if child.get('pythonObjectTypeName') == 'SpaceObjectIcon':
            ei = child.get('dictEntriesOfInterest', {})
            coords = get_node_coords(child)
            result['x'] = coords['x']
            result['y'] = coords['y']
            
            # Ищем иконку
            for sub in child.get('children', []):
                if sub.get('pythonObjectTypeName') == 'Sprite':
                    texture = sub.get('dictEntriesOfInterest', {}).get('_texturePath', '')
                    result['icon'] = str(texture)
                    # Определяем тип по иконке
                    if 'station' in str(texture):
                        result['type'] = 'station'
                    elif 'stargate' in str(texture):
                        result['type'] = 'stargate'
                    elif 'asteroid' in str(texture):
                        result['type'] = 'asteroid'
                    elif 'planet' in str(texture):
                        result['type'] = 'planet'
                    elif 'citadel' in str(texture):
                        result['type'] = 'citadel'
    
    return result if result['name'] else None


# ==================== ПАРСЕР ЛОКАЛЬНОГО ЧАТА ====================

def parse_local_chat(root: Dict) -> Dict[str, Any]:
    """Парсинг локального чата"""
    result = {
        'player_count': 0,
        'has_neutral': False,
        'has_hostile': False,
        'players': []
    }
    
    # Ищем ChatWindowStack
    chat_stack = find_first_by_type(root, 'ChatWindowStack')
    if not chat_stack:
        return result
    
    # Ищем XmppChatWindow внутри chat_stack
    xmpp_chat = find_first_by_type(chat_stack, 'XmppChatWindow')
    if not xmpp_chat:
        return result
    
    # Ищем контейнер content внутри XmppChatWindow
    content_container = None
    for child in xmpp_chat.get('children', []):
        if child.get('pythonObjectTypeName') == 'Container':
            if child.get('dictEntriesOfInterest', {}).get('_name') == 'content':
                content_container = child
                break
    
    if not content_container:
        return result
    
    # Ищем контейнер main внутри content
    main_container = None
    for child in content_container.get('children', []):
        if child.get('pythonObjectTypeName') == 'Container':
            if child.get('dictEntriesOfInterest', {}).get('_name') == 'main':
                main_container = child
                break
    
    if not main_container:
        return result
    
    # ВАЖНО: в main_container есть Container с userlist_divider и BasicDynamicScroll
    # Ищем контейнер, который содержит BasicDynamicScroll с userlist
    userlist_scroll = None
    for child in main_container.get('children', []):
        # Это контейнер с шириной 136 (правая панель чата)
        if child.get('pythonObjectTypeName') == 'Container':
            # Проверяем, есть ли внутри Divider с именем user_list_divider
            has_userlist_divider = False
            for sub in child.get('children', []):
                if sub.get('pythonObjectTypeName') == 'Divider':
                    if sub.get('dictEntriesOfInterest', {}).get('_name') == 'user_list_divider':
                        has_userlist_divider = True
                        break
            
            if has_userlist_divider:
                # Теперь ищем BasicDynamicScroll внутри этого контейнера
                for sub in child.get('children', []):
                    if sub.get('pythonObjectTypeName') == 'BasicDynamicScroll':
                        userlist_scroll = sub
                        break
                break
    
    if not userlist_scroll:
        return result
    
    # Рекурсивно собираем всех XmppChatSimpleUserEntry
    def collect_user_entries(obj):
        entries = []
        if isinstance(obj, dict):
            if obj.get('pythonObjectTypeName') == 'XmppChatSimpleUserEntry':
                entries.append(obj)
            for child in obj.get('children', []):
                entries.extend(collect_user_entries(child))
        return entries
    
    user_entries = collect_user_entries(userlist_scroll)
    
    for user_entry in user_entries:
        parsed = parse_chat_user(user_entry)
        if parsed:
            result['players'].append(parsed)
    
    result['player_count'] = len(result['players'])
    
    # Определяем наличие нейтралов и хостов
    for player in result['players']:
        if player.get('standing') == 'NEUTRAL':
            result['has_neutral'] = True
        if player.get('standing') == 'HOSTILE':
            result['has_hostile'] = True
    
    return result

def parse_chat_user(user_node: Dict, is_self: bool = False) -> Optional[Dict]:
    """Парсинг одного пользователя чата"""
    ei = user_node.get('dictEntriesOfInterest', {})
    result = {
        'name': str(ei.get('_name', '')),
        'standing': 'UNKNOWN',
        'alliance': False,
        'is_self': is_self  # Добавляем флаг "это я"
    }
    
    if not result['name'] or result['name'] in ['', 'local', 'Local']:
        return None
    
    # Если это сам игрок - пропускаем
    if is_self:
        return None
    
    # Рекурсивно ищем FlagIconWithState
    def find_flag_icon(obj):
        if isinstance(obj, dict):
            if obj.get('pythonObjectTypeName') == 'FlagIconWithState':
                return obj
            for child in obj.get('children', []):
                found = find_flag_icon(child)
                if found:
                    return found
        return None
    
    flag_icon = find_flag_icon(user_node)
    
    if flag_icon:
        hint = str(flag_icon.get('dictEntriesOfInterest', {}).get('_hint', '')).lower()
        
        if 'neutral' in hint or 'no standing' in hint:
            result['standing'] = 'NEUTRAL'
        elif 'terrible' in hint or 'bad' in hint or 'hostile' in hint:
            result['standing'] = 'HOSTILE'
        elif 'alliance' in hint:
            result['alliance'] = True
            result['standing'] = 'ALLIANCE'
        elif 'excellent' in hint or 'good' in hint:
            result['standing'] = 'GOOD'
        elif 'corp' in hint or 'corporation' in hint:
            result['standing'] = 'CORPORATION'
    else:
        # Если нет флажка - это сам игрок (как Nikky Naari)
        # Возвращаем None, чтобы не включать в список
        return None
    
    return result


def parse_local_chat(root: Dict) -> Dict[str, Any]:
    """Парсинг локального чата"""
    result = {
        'player_count': 0,
        'has_neutral': False,
        'has_hostile': False,
        'players': []
    }
    
    # Ищем ChatWindowStack
    chat_stack = find_first_by_type(root, 'ChatWindowStack')
    if not chat_stack:
        return result
    
    # Ищем XmppChatWindow внутри chat_stack
    xmpp_chat = find_first_by_type(chat_stack, 'XmppChatWindow')
    if not xmpp_chat:
        return result
    
    # Ищем контейнер content внутри XmppChatWindow
    content_container = None
    for child in xmpp_chat.get('children', []):
        if child.get('pythonObjectTypeName') == 'Container':
            if child.get('dictEntriesOfInterest', {}).get('_name') == 'content':
                content_container = child
                break
    
    if not content_container:
        return result
    
    # Ищем контейнер main внутри content
    main_container = None
    for child in content_container.get('children', []):
        if child.get('pythonObjectTypeName') == 'Container':
            if child.get('dictEntriesOfInterest', {}).get('_name') == 'main':
                main_container = child
                break
    
    if not main_container:
        return result
    
    # Ищем userlist_scroll
    userlist_scroll = None
    for child in main_container.get('children', []):
        if child.get('pythonObjectTypeName') == 'Container':
            has_userlist_divider = False
            for sub in child.get('children', []):
                if sub.get('pythonObjectTypeName') == 'Divider':
                    if sub.get('dictEntriesOfInterest', {}).get('_name') == 'user_list_divider':
                        has_userlist_divider = True
                        break
            
            if has_userlist_divider:
                for sub in child.get('children', []):
                    if sub.get('pythonObjectTypeName') == 'BasicDynamicScroll':
                        userlist_scroll = sub
                        break
                break
    
    if not userlist_scroll:
        return result
    
    # Рекурсивно собираем всех XmppChatSimpleUserEntry
    def collect_user_entries(obj):
        entries = []
        if isinstance(obj, dict):
            if obj.get('pythonObjectTypeName') == 'XmppChatSimpleUserEntry':
                entries.append(obj)
            for child in obj.get('children', []):
                entries.extend(collect_user_entries(child))
        return entries
    
    user_entries = collect_user_entries(userlist_scroll)
    
    for user_entry in user_entries:
        # Определяем, является ли пользователь самим игроком (отсутствие флажка)
        ei = user_entry.get('dictEntriesOfInterest', {})
        name = str(ei.get('_name', ''))
        
        # Проверяем наличие флажка
        has_flag = False
        def check_flag(obj):
            nonlocal has_flag
            if isinstance(obj, dict):
                if obj.get('pythonObjectTypeName') == 'FlagIconWithState':
                    has_flag = True
                    return
                for child in obj.get('children', []):
                    check_flag(child)
        
        check_flag(user_entry)
        
        # Если нет флажка - это сам игрок, пропускаем
        if not has_flag:
            continue
        
        parsed = parse_chat_user(user_entry)
        if parsed:
            result['players'].append(parsed)
    
    result['player_count'] = len(result['players'])
    
    # Определяем наличие нейтралов и хостов
    for player in result['players']:
        if player.get('standing') == 'NEUTRAL':
            result['has_neutral'] = True
        if player.get('standing') == 'HOSTILE':
            result['has_hostile'] = True
    
    return result


# ==================== ПАРСЕР ПРОБ-СКАНЕРА ====================

def parse_scan_results(root: Dict) -> List[Dict]:
    """Парсинг результатов сканирования"""
    result = []
    
    scanner_wnd = find_first_by_type(root, 'ProbeScannerWindow')
    if not scanner_wnd:
        return result
    
    # Ищем результаты
    for child in scanner_wnd.get('children', []):
        if child.get('pythonObjectTypeName') == 'Container':
            if child.get('dictEntriesOfInterest', {}).get('_name') == 'content':
                for sub in child.get('children', []):
                    if sub.get('pythonObjectTypeName') == 'Container':
                        if sub.get('dictEntriesOfInterest', {}).get('_name') == 'main':
                            for palette in sub.get('children', []):
                                if palette.get('pythonObjectTypeName') == 'ProbeScannerPalette':
                                    for scan_results in palette.get('children', []):
                                        if scan_results.get('pythonObjectTypeName') == 'Container':
                                            if scan_results.get('dictEntriesOfInterest', {}).get('_name') == 'ScanResultsContainer':
                                                for scan in scan_results.get('children', []):
                                                    if scan.get('pythonObjectTypeName') == 'ScanResults':
                                                        for entry in scan.get('children', []):
                                                            if entry.get('pythonObjectTypeName') == 'Container':
                                                                if entry.get('dictEntriesOfInterest', {}).get('_name') == 'maincontainer':
                                                                    for clipper in entry.get('children', []):
                                                                        if clipper.get('pythonObjectTypeName') == 'Container':
                                                                            for content in clipper.get('children', []):
                                                                                if content.get('pythonObjectTypeName') == 'Container':
                                                                                    for scan_result in content.get('children', []):
                                                                                        if scan_result.get('pythonObjectTypeName') == 'ScanResultNew':
                                                                                            parsed = parse_scan_result(scan_result)
                                                                                            if parsed:
                                                                                                result.append(parsed)
    
    return result


def parse_scan_result(scan_node: Dict) -> Optional[Dict]:
    """Парсинг одного результата сканирования"""
    result = {
        'id': '',
        'name': '',
        'group': '',
        'distance': 0.0,
        'signal': 0.0,
        'type': 'signature'
    }
    
    for child in scan_node.get('children', []):
        if child.get('pythonObjectTypeName') == 'Container':
            for sub in child.get('children', []):
                if sub.get('pythonObjectTypeName') == 'EveLabelMedium':
                    text = get_node_text(sub)
                    if '%' in text:
                        result['signal'] = parse_percent(text)
                    elif 'AU' in text or 'km' in text:
                        result['distance'] = parse_distance(text)
                    elif len(text) >= 7 and '-' in text and len(text) < 15:
                        result['id'] = text
                    elif text in ['Ore Site', 'Combat Site', 'Cosmic Signature', 'Relic Site', 'Data Site']:
                        result['group'] = text
                    elif text and not result['name']:
                        result['name'] = text
                
                if sub.get('pythonObjectTypeName') == 'Sprite':
                    texture = str(sub.get('dictEntriesOfInterest', {}).get('_texturePath', ''))
                    if 'ore_site' in texture:
                        result['type'] = 'ore'
                    elif 'combatSite' in texture:
                        result['type'] = 'combat'
                    elif 'scanResultLocation' in texture:
                        result['type'] = 'signature'
    
    return result if result['id'] or result['name'] else None


# ==================== ПАРСЕР ИНФО-ПАНЕЛИ ====================

def parse_side_panel(root: Dict) -> Dict[str, Any]:
    """Парсинг боковой панели информации"""
    result = {
        'current_system': '',
        'security_status': 0.0,
        'nearest_location': '',
        'sovereignty_holder': '',
        'route_destination': '',
        'bounty_risk': 0.0
    }
    
    # Ищем SidePanels
    side_panels = find_first_by_type(root, 'SidePanels')
    if not side_panels:
        return result
    
    # Ищем info panel
    info_panel = find_first_by_type(side_panels, 'InfoPanelContainer')
    if not info_panel:
        return result
    
    # Ищем LocationInfo
    for child in info_panel.get('children', []):
        if child.get('pythonObjectTypeName') == 'ContainerAutoSize':
            if child.get('dictEntriesOfInterest', {}).get('_name') == 'mainCont':
                for sub in child.get('children', []):
                    if sub.get('pythonObjectTypeName') == 'InfoPanelLocationInfo':
                        # Парсим системную информацию
                        for label in sub.get('children', []):
                            if label.get('pythonObjectTypeName') == 'ContainerAutoSize':
                                if label.get('dictEntriesOfInterest', {}).get('_name') == 'headerCont':
                                    for row in label.get('children', []):
                                        if row.get('pythonObjectTypeName') == 'ContainerAutoSize':
                                            for content in row.get('children', []):
                                                if content.get('pythonObjectTypeName') == 'Container':
                                                    for item in content.get('children', []):
                                                        if item.get('pythonObjectTypeName') == 'EveCaptionSmall':
                                                            text = get_node_text(item)
                                                            if '4GQ-XQ' in text or 'System' in text:
                                                                # Это имя системы
                                                                result['current_system'] = clean_html(text)
                                                            elif 'Security status' in text:
                                                                # Парсим статус безопасности
                                                                match = re.search(r'([\d.]+)', text)
                                                                if match:
                                                                    result['security_status'] = float(match.group(1))
                    elif sub.get('pythonObjectTypeName') == 'InfoPanelRoute':
                        # Парсим маршрут
                        for route_child in sub.get('children', []):
                            if route_child.get('pythonObjectTypeName') == 'ContainerAutoSize':
                                if route_child.get('dictEntriesOfInterest', {}).get('_name') == 'mainCont':
                                    for label in route_child.get('children', []):
                                        if label.get('pythonObjectTypeName') == 'EveLabelMedium':
                                            text = get_node_text(label)
                                            if 'No Destination' not in text:
                                                result['route_destination'] = text
    
    return result


# ==================== ПАРСЕР НЕОКОМА ====================

def parse_neocom(root: Dict) -> Dict[str, Any]:
    """Парсинг неокома"""
    result = {
        'buttons': [],
        'clock': '',
        'notifications': {}
    }
    
    neocom = find_first_by_type(root, 'NeocomContainer')
    if not neocom:
        return result
    
    # Ищем кнопки
    for child in neocom.get('children', []):
        if child.get('pythonObjectTypeName') == 'Container':
            if child.get('dictEntriesOfInterest', {}).get('_name') == 'mainCont':
                for sub in child.get('children', []):
                    if sub.get('pythonObjectTypeName') == 'Container':
                        if sub.get('dictEntriesOfInterest', {}).get('_name') == 'buttonCont':
                            for button in sub.get('children', []):
                                if 'Button' in button.get('pythonObjectTypeName', ''):
                                    ei = button.get('dictEntriesOfInterest', {})
                                    btn_info = {
                                        'name': str(ei.get('_name', '')),
                                        'has_notification': False,
                                        'notification_count': 0
                                    }
                                    
                                    # Проверяем индикатор
                                    for indicator in button.get('children', []):
                                        if indicator.get('pythonObjectTypeName') == 'ButtonIndicator':
                                            ind_ei = indicator.get('dictEntriesOfInterest', {})
                                            if ind_ei.get('_opacity', 0) > 0:
                                                btn_info['has_notification'] = True
                                                # Ищем количество
                                                for content in indicator.get('children', []):
                                                    if content.get('pythonObjectTypeName') == 'Container':
                                                        for label in content.get('children', []):
                                                            if label.get('pythonObjectTypeName') == 'EveLabelMedium':
                                                                text = get_node_text(label)
                                                                if text.isdigit():
                                                                    btn_info['notification_count'] = int(text)
                                    
                                    result['buttons'].append(btn_info)
        
        # Ищем часы
        if child.get('pythonObjectTypeName') == 'Container':
            for sub in child.get('children', []):
                if sub.get('pythonObjectTypeName') == 'ClockButton':
                    for clock in sub.get('children', []):
                        if clock.get('pythonObjectTypeName') == 'InGameClock':
                            for label in clock.get('children', []):
                                if label.get('pythonObjectTypeName') == 'Label':
                                    text = get_node_text(label)
                                    if 'clock_label' in str(label.get('dictEntriesOfInterest', {}).get('_name', '')):
                                        result['clock'] = clean_html(text)
    
    return result


# ==================== ГЛАВНЫЙ ПАРСЕР ====================

# ==================== ГЛАВНЫЙ ПАРСЕР ====================

def parse_ui_root(json_data: Dict) -> ParsedUIState:
    """Главная функция парсинга"""
    state = ParsedUIState()
    state.timestamp = time.time()
    state.ui_root_address = json_data.get('pythonObjectAddress', 0)
    
    root = json_data
    
    # Базовые параметры
    root_ei = root.get('dictEntriesOfInterest', {})
    state.window_width = get_val(root_ei.get('_displayWidth', 0))
    state.window_height = get_val(root_ei.get('_displayHeight', 0))
    
    # ====== КОНТЕКСТНОЕ МЕНЮ ======
    menu_data = parse_context_menu(root)
    state.menu_open = menu_data['open']
    state.menu_type = menu_data['menu_type']
    state.menu_items = menu_data['items']
    
    # ====== HP КОРАБЛЯ ======
    hp_data = parse_hp_gauges(root)
    state.shield_pct = hp_data['shield']
    state.armor_pct = hp_data['armor']
    state.structure_pct = hp_data['structure']
    state.capacitor_pct = hp_data['capacitor']
    
    # ====== СКОРОСТЬ ======
    state.speed = parse_speed(root)
    
    # ====== МОДУЛИ ======
    modules_data = parse_modules(root)
    state.modules = modules_data['modules']
    state.high_slots = modules_data['high_slots']
    state.medium_slots = modules_data['medium_slots']
    state.low_slots = modules_data['low_slots']
    
    # ====== ДРОНЫ ======
    drones_data = parse_drones(root)
    state.drones_in_bay = drones_data['in_bay']
    state.drones_in_space = drones_data['in_space']
    state.drones_max = drones_data['max']
    state.drones_idle = drones_data['idle']
    state.drones_fighting = drones_data['fighting']
    state.drones_returning = drones_data['returning']
    state.drones_under_attack = drones_data['under_attack']
    state.drone_entries = drones_data['entries']
    
    # ====== ЦЕЛИ ======
    targets_data = parse_targets(root)
    state.targets = targets_data['targets']
    state.active_target = targets_data['active_target']
    
    # ====== ВЫБРАННЫЙ ОБЪЕКТ ======
    selected = parse_selected_item(root)
    if selected:
        state.selected_item = selected
    
    # ====== ОВЕРВЬЮ ======
    state.overview_entries = parse_overview(root)
    
    # ====== ЛОКАЛЬНЫЙ ЧАТ ======
    chat_data = parse_local_chat(root)
    state.local_player_count = chat_data['player_count']
    state.local_has_neutral = chat_data['has_neutral']
    state.local_has_hostile = chat_data['has_hostile']
    state.local_players = chat_data['players']
    
    # ====== ДЕБАГ ВЫВОД ЛОКАЛА ======
    print(f"\n=== LOCAL CHAT DEBUG ===")
    print(f"Players found: {len(state.local_players)}")
    print(f"Player count from header: {state.local_player_count}")
    print(f"Has neutral: {state.local_has_neutral}")
    print(f"Has hostile: {state.local_has_hostile}")
    for p in state.local_players:
        print(f"  - {p['name']}: {p['standing']} (Alliance: {p.get('alliance', False)})")
    
    # ====== ПРОБ-СКАНЕР ======
    state.scan_results = parse_scan_results(root)
    
    # ====== ИНФО-ПАНЕЛЬ ======
    panel_data = parse_side_panel(root)
    state.current_system = panel_data['current_system']
    state.security_status = panel_data['security_status']
    state.nearest_location = panel_data['nearest_location']
    state.sovereignty_holder = panel_data['sovereignty_holder']
    state.route_destination = panel_data['route_destination']
    state.bounty_risk = panel_data['bounty_risk']
    
    # ====== НЕОКОМ ======
    neocom_data = parse_neocom(root)
    state.neocom_buttons = neocom_data['buttons']
    state.clock_time = neocom_data['clock']
    
    # ====== ДОПОЛНИТЕЛЬНО ======
    # Проверяем, открыт ли экран загрузки
    loading_fill = find_first_by_type(root, 'Fill')
    if loading_fill:
        ei = loading_fill.get('dictEntriesOfInterest', {})
        color = ei.get('_color', {})
        if color.get('aPercent', 0) == 0 and color.get('rPercent', 0) == 0:
            state.is_loading = True
    
    # ====== СТАТУС КОРАБЛЯ (warp / aligning / orbit / docked) ======
    # Реальная структура индикации:
    #   ShipUI -> HudContainer -> HudActionIndicationContainer
    #     -> IndicationMessageStandard -> TextBody('indication_text', _setText='Warp Drive Active')
    indication_container = find_first_by_type(root, 'HudActionIndicationContainer')
    if indication_container:
        texts = []
        def collect_texts(node):
            if not isinstance(node, dict):
                return
            ei = node.get('dictEntriesOfInterest', {}) or {}
            raw = ei.get('_setText') or ei.get('_text') or ''
            if raw:
                texts.append(clean_html(str(raw)))
            for child in node.get('children', []) or []:
                collect_texts(child)
        collect_texts(indication_container)
        joined = ' | '.join(texts).lower()
        if 'warp drive active' in joined or 'warping' in joined or 'warped' in joined:
            state.is_warp = True
        if 'establishing warp vector' in joined or 'aligning' in joined or 'align' in joined:
            state.is_establishing = True
        if 'orbiting' in joined:
            state.is_orbiting = True
        if 'docked' in joined:
            state.is_docked = True

    # Fallback 1: скорость в варпе огромная
    if not state.is_warp and state.speed > 100000:
        state.is_warp = True

    # Fallback 2: docked - наличие интерфейса станции
    if not state.is_docked:
        if find_first_by_type(root, 'UndockButton') or find_first_by_type(root, 'HangarLayer'):
            state.is_docked = True

    return state

# ==================== ПРИМЕР ИСПОЛЬЗОВАНИЯ ====================

def main():
    # Загрузка JSON
    with open('uiroot_new.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Парсинг
    state = parse_ui_root(data)
    
    # Вывод результатов
    print(f"=== EVE UI PARSER RESULTS ===")
    print(f"Window: {state.window_width}x{state.window_height}")
    print(f"Menu open: {state.menu_open} ({state.menu_type})")
    def _fmt_hp(v):
        return f"{v:.1f}" if isinstance(v, (int, float)) else "n/a"
    print(f"HP: Shield={_fmt_hp(state.shield_pct)}% Armor={_fmt_hp(state.armor_pct)}% "
          f"Structure={_fmt_hp(state.structure_pct)}% Cap={_fmt_hp(state.capacitor_pct)}%")

    print(f"Speed: {state.speed:.1f} m/s")
    print(f"Drones: Bay={state.drones_in_bay} Space={state.drones_in_space}/{state.drones_max}")
    print(f"Local: {state.local_player_count} players, Neutral={state.local_has_neutral}, Hostile={state.local_has_hostile}")
    print(f"Targets: {len(state.targets)}")
    print(f"Overview: {len(state.overview_entries)} entries")
    print(f"Scan results: {len(state.scan_results)}")
    print(f"System: {state.current_system} (Sec: {state.security_status})")
    print(f"Time: {state.clock_time}")
    
    # Детальный вывод меню
    if state.menu_open:
        print("\n=== MENU ITEMS ===")
        for item in state.menu_items:
            if item.get('type') == 'entry':
                print(f"  [{item['text']}] at ({item['x']}, {item['y']}) w={item['w']} h={item['h']} submenu={item['has_submenu']}")
            else:
                print("  ---")
    
    # Детальный вывод овервью
    print("\n=== OVERVIEW ===")
    for entry in state.overview_entries[:5]:
        print(f"  {entry['name']} - {entry['distance']:.0f}m [{entry['type']}]")
    
    # Детальный вывод сканов
    print("\n=== SCAN RESULTS ===")
    for scan in state.scan_results[:5]:
        print(f"  {scan['id']} - {scan['name']} ({scan['group']}) {scan['distance']:.2f}AU Signal: {scan['signal']}%")

if __name__ == "__main__":
    main()