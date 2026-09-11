from __future__ import annotations  
from ParsedUIState import ParsedUIState
import json
import re
import time
from typing import Dict, Any, List, Optional, Tuple
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


# ==================== МОДАЛЬНЫЕ ОКНА (INFORMATION) ====================

def parse_modal_info(root: Dict) -> Dict[str, Any]:
    """Детект модального окна 'Information' (Telecom) и его кнопки Close.

    EVE рисует модальные окна (например 'Information' про неевклидовы
    аномалии/контент) как узел типа 'Telecom' (400x400) ПО ПОВЕРХ
    остального UI. Окно блокирует дальнейший ввод — его нужно закрыть
    (Enter или кнопка Close) до продолжения работы.

    Структура (raw_dump_20260902_184332.json):
      Telecom(name='telecom') @ (569,299,400x400)
        -> Container('window_controls_cont')
          -> DefaultWindowControls
            -> WindowControls
              -> ContainerAutoSize
                -> WindowControlButton
                  -> ButtonIcon('CloseButtonIcon') @ (939,305,24x24)
        -> Container('content')
          -> ... main ...
          -> Container('bottom')
            -> ButtonGroup
              -> ButtonWrapper
                -> Button('ok_dialog_button')
                  -> Label('Close') @ (...)

    Возвращает:
      {
        'open': bool,           # есть модальное окно Telecom с заголовком Information
        'name': str,            # имя окна (из WindowCaption / иконки)
        'x': int, 'y': int,     # координаты окна (накопленные от корня)
        'w': int, 'h': int,     # размер окна
        'close': None | {'x': int, 'y': int, 'w': int, 'h': int}  # кнопка Close
      }
    """
    result: Dict[str, Any] = {
        'open': False, 'name': '', 'x': 0, 'y': 0, 'w': 0, 'h': 0,
        'close': None,
    }

    # Ищем узел 'Telecom' по ВСЕМУ дереву (модалка поверх, имя 'telecom')
    def _find(node: Dict) -> Optional[Dict]:
        if not isinstance(node, dict):
            return None
        if node.get('pythonObjectTypeName') == 'Telecom':
            return node
        for child in node.get('children', []) or []:
            found = _find(child)
            if found:
                return found
        return None

    telecom_node = _find(root)
    if not telecom_node:
        return result

    tc = telecom_node.get('dictEntriesOfInterest', {}) or {}
    tx = get_val(tc.get('_displayX', 0))
    ty = get_val(tc.get('_displayY', 0))
    result['x'], result['y'] = tx, ty
    result['w'] = get_val(tc.get('_displayWidth', 0))
    result['h'] = get_val(tc.get('_displayHeight', 0))

    # Ищем заголовок Information (WindowCaption -> Label _setText='Information')
    # и кнопку Close, накапливая координаты от Telecom вниз.
    header_text = ''  # НЕ брать имя Telecom ('telecom') — ищем именно WindowCaption
    close_btn = None

    def walk(node: Dict, px: int, py: int) -> None:
        nonlocal header_text, close_btn
        # Не прекращаем раньше времени: нижняя кнопка может быть глубже в дереве,
        # чем заголовок и верхняя иконка. Ищем, пока не найдём нижнюю кнопку
        # (предпочтительнее для пользователя) или не закончится дерево.
        ei = node.get('dictEntriesOfInterest', {}) or {}
        t = node.get('pythonObjectTypeName', '')
        x = px + get_val(ei.get('_displayX', 0))
        y = py + get_val(ei.get('_displayY', 0))
        w = get_val(ei.get('_displayWidth', 0))
        h = get_val(ei.get('_displayHeight', 0))

        if not header_text and t == 'WindowCaption':
            # в поддереве ищем Label с текстом (например 'Information')
            for sub in _walk_all(node):
                sei = sub.get('dictEntriesOfInterest', {}) or {}
                txt = str(sei.get('_setText', '') or '')
                if txt.strip():
                    header_text = txt.strip()
                    break

        # Кнопка Close: либо ButtonIcon (верхняя иконка), либо Button 'ok_dialog_button' (нижняя большая)
        # Предпочитаем нижнюю кнопку — она более заметна для пользователя
        if t == 'ButtonIcon':
            nm = str(ei.get('_name', ''))
            hint = str(ei.get('_hint', ''))
            if 'close' in nm.lower() or 'close' in hint.lower():
                # Если уже есть нижняя кнопка — не перезаписываем
                if close_btn is None or close_btn.get('type') != 'button':
                    close_btn = {'x': x, 'y': y, 'w': w, 'h': h, 'type': 'icon'}
        elif t == 'Button' and str(ei.get('_name', '')) == 'ok_dialog_button':
            # нижняя большая кнопка "Close" — предпочитаем её (перезаписываем иконку)
            close_btn = {'x': x, 'y': y, 'w': w, 'h': h, 'type': 'button'}

        for child in node.get('children', []) or []:
            walk(child, x, y)

    walk(telecom_node, 0, 0)

    result['name'] = header_text
    # Модалка считается "открытой", только если это окно Information с заголовком
    if 'information' in header_text.lower():
        result['open'] = True
        # Убираем служебное поле 'type' из close_btn перед возвратом
        if close_btn:
            close_btn_clean = {k: v for k, v in close_btn.items() if k != 'type'}
            result['close'] = close_btn_clean
    else:
        result['open'] = False
        result['close'] = None

    return result


def _walk_all(node):
    """Генератор всех узлов в поддереве (dict), включая сам node."""
    if isinstance(node, dict):
        yield node
        for child in node.get('children', []) or []:
            yield from _walk_all(child)


# ==================== ПАРСЕР КОНТЕКСТНОГО МЕНЮ ====================

def parse_context_menu(root: Dict) -> Dict[str, Any]:
    """Парсинг контекстного меню.

    l_menu может содержать одновременно ContextMenu (основное меню) и
    ContextSubMenu (раскрытое подменю, например дистанции варпа).
    Собираем пункты из ВСЕХ окон меню в один список — бот ищет нужный
    пункт по тексту ('Warp to Location', 'within 20 km', ...) по всему списку.
    Координаты пунктов — АБСОЛЮТНЫЕ (накоплением от окна меню).
    """
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
    
    # Собираем ContextMenu и ContextSubMenu (может быть несколько окон)
    for child in l_menu.get('children', []):
        obj_type = child.get('pythonObjectTypeName', '')
        if obj_type in ['ContextMenu', 'ContextSubMenu']:
            result['open'] = True
            if not result['menu_type']:
                result['menu_type'] = obj_type
            wc = get_node_coords(child)
            items = parse_menu_items(child, wc['x'], wc['y'])
            result['items'].extend(items)
            if obj_type == 'ContextSubMenu':
                result['submenus'].append(items)
    
    return result


def parse_menu_items(menu_node: Dict, px: int = 0, py: int = 0) -> List[Dict]:
    """Парсинг пунктов меню (px, py — координаты окна меню для абсолютизации)"""
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
    
    # Накопляем смещение mainCont внутри окна меню
    cei = main_cont.get('dictEntriesOfInterest', {}) or {}
    mx = px + get_val(cei.get('_displayX', 0))
    my = py + get_val(cei.get('_displayY', 0))
    
    for child in main_cont.get('children', []):
        if child.get('pythonObjectTypeName') == 'MenuEntryView':
            items.append(parse_menu_entry(child, mx, my))
        elif child.get('pythonObjectTypeName') == 'Line':
            items.append({'type': 'separator'})
    
    return items


def parse_menu_entry(entry_node: Dict, px: int = 0, py: int = 0) -> Dict:
    """Парсинг одного пункта меню (px, py — координаты родителя)"""
    ei = entry_node.get('dictEntriesOfInterest', {})
    coords = get_node_coords(entry_node, px, py)
    
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


# ==================== ПАРСЕР ASTEROID BELTS (КОНТЕКСТНОЕ МЕНЮ) ====================

# '<Планета> - Asteroid Belt <N>' (например '4GQ-XQ III - Asteroid Belt 1')
_ASTEROID_BELT_NUM_RE = re.compile(r'(?i)asteroid belt\s*#?\s*(\d+)\s*$')


def parse_asteroid_belts(root: Dict) -> List[Dict]:
    """Список астероидных поясов из открытого контекстного меню.

    При правом клике в космосе меню содержит подменю 'Asteroid Belts'
    с записями вида '<Планета> - Asteroid Belt <N>' (например
    '4GQ-XQ III - Asteroid Belt 1'). Записи лежат в слое l_menu как
    MenuEntryView с _name='context_menu_<текст>' и TextBody(_setText=текст).

    Возвращает список словарей:
      {'name': полный текст пункта, 'planet': планета, 'belt': номер,
       'x':, 'y':, 'w':, 'h': координаты пункта в окне (для клика)}
    Пункт 'Asteroid Belts' (открывашка подменю) и 'Add Asteroid Belt to
    Overview' в список не попадают (номер пояса отсутствует).
    """
    belts: List[Dict] = []
    l_menu = None
    for child in root.get('children', []):
        if child.get('dictEntriesOfInterest', {}).get('_name') == 'l_menu':
            l_menu = child
            break

    if not l_menu:
        return belts

    def walk(node: Dict, ox: int, oy: int) -> None:
        if not isinstance(node, dict):
            return
        ei = node.get('dictEntriesOfInterest', {}) or {}
        # накапливаем смещения контейнеров, чтобы получить координаты в окне
        nox = ox + get_val(ei.get('_displayX', 0))
        noy = oy + get_val(ei.get('_displayY', 0))

        if node.get('pythonObjectTypeName') == 'MenuEntryView':
            name = str(ei.get('_name', ''))
            if name.startswith('context_menu_'):
                text = name[len('context_menu_'):]
                # предпочитаем текст из TextBody (чистый текст)
                for ch in node.get('children', []) or []:
                    if ch.get('pythonObjectTypeName') == 'TextBody':
                        tb = clean_html(get_node_text(ch))
                        if tb:
                            text = tb
                        break
                m = _ASTEROID_BELT_NUM_RE.search(text)
                if m:
                    planet = text[:m.start()].strip().rstrip('-').strip()
                    belts.append({
                        'name': text.strip(),
                        'planet': planet,
                        'belt': int(m.group(1)),
                        'x': nox,
                        'y': noy,
                        'w': get_val(ei.get('_displayWidth', 0)),
                        'h': get_val(ei.get('_displayHeight', 0)),
                    })

        for ch in node.get('children', []) or []:
            walk(ch, nox, noy)

    for child in l_menu.get('children', []) or []:
        walk(child, 0, 0)

    # стабильная сортировка: планета, затем номер пояса
    belts.sort(key=lambda b: (str(b['planet']), int(b['belt'])))
    return belts


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
    = 72 ячейки. Семантика (проверена на дампах с разным уровнем капы):
      * ВИДИМАЯ ячейка (_color.aPercent > 0) — это РАЗРЯЖЕННАЯ (тёмная) часть
        кольца; заполненная часть рисуется яркой дугой, и ячейки у неё в
        дампе прозрачны (aPercent == 0);
      * 'powerblink' (capacitorCellGlow.png) — мерцающая ячейка на границе
        разряда/заряда (фронтир), в подсчёте не участвует.

    Процент = 100 - (видимые ячейки / 72) * 100.
    Знаменатель фиксирован (72), потому что дампер захватывает не все 18
    колонок сразу, а лишь часть (в дампе при почти полном капе было 3
    колонки из 18). Точность — одна ячейка (~1.4%).

    Fallback: если 'pmark' не найдены, пробуем любой дочерний узел с
    _lastValue (на случай изменения структуры дампа в будущих патчах).
    """
    depleted = 0
    for col in cap_node.get('children', []) or []:
        if not isinstance(col, dict):
            continue
        for spr in col.get('children', []) or []:
            if not isinstance(spr, dict):
                continue
            ei = spr.get('dictEntriesOfInterest', {}) or {}
            if ei.get('_name') != 'pmark':
                continue
            color = ei.get('_color') or {}
            alpha = _to_float(color.get('aPercent'))
            if alpha is not None and alpha > 0:
                depleted += 1
    if depleted > 0 or _count_pmarks(cap_node) > 0:
        cap = 100.0 - depleted / 72.0 * 100.0
        return max(0.0, min(100.0, cap))

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


def _count_pmarks(cap_node: Dict) -> int:
    """Количество ячеек 'pmark', присутствующих в захваченном поддереве."""
    count = 0
    for col in cap_node.get('children', []) or []:
        if not isinstance(col, dict):
            continue
        for spr in col.get('children', []) or []:
            if isinstance(spr, dict) and \
                    (spr.get('dictEntriesOfInterest') or {}).get('_name') == 'pmark':
                count += 1
    return count


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
    = 72 ячейки внутри CapacitorContainer. Видимая ячейка (_color.aPercent>0)
    — РАЗРЯЖЕННАЯ часть кольца, 'powerblink' (capacitorCellGlow.png) —
    мерцающий фронтир разряда/заряда. Процент = 100 - видимые/72*100.
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
    """Парсинг модулей HUD.

    Для каждого слота с модулем собираем: имя, активность (ramp_active),
    иконку и АБСОЛЮТНЫЕ координаты кнопки модуля (x/y — центр, накоплением
    от корня дерева): бот включает модули КЛИКОМ по иконке, т.к. у средних
    и нижних слотов нет дефолтных хоткеев (F1..F8 только у высоких).
    """
    result = {
        'modules': {},
        'high_slots': [],
        'medium_slots': [],
        'low_slots': []
    }

    def walk(node: Dict, px: float, py: float) -> None:
        if not isinstance(node, dict):
            return
        ei = node.get('dictEntriesOfInterest', {}) or {}
        ax = px + get_val(ei.get('_displayX'))
        ay = py + get_val(ei.get('_displayY'))

        if node.get('pythonObjectTypeName') == 'ShipSlot':
            slot_name = str(ei.get('_name', ''))
            slot_type = 'unknown'
            if 'High' in slot_name:
                slot_type = 'high'
            elif 'Medium' in slot_name:
                slot_type = 'medium'
            elif 'Low' in slot_name:
                slot_type = 'low'

            is_active = False
            icon = None
            btn = None

            for sub in node.get('children', []) or []:
                if sub.get('pythonObjectTypeName') != 'ModuleButton':
                    continue
                sub_ei = sub.get('dictEntriesOfInterest', {}) or {}
                if sub_ei.get('ramp_active') is True:
                    is_active = True
                # Координаты кнопки модуля (абсолютные, центр)
                bx = ax + get_val(sub_ei.get('_displayX'))
                by = ay + get_val(sub_ei.get('_displayY'))
                bw = get_val(sub_ei.get('_displayWidth', 48))
                bh = get_val(sub_ei.get('_displayHeight', 48))
                btn = {
                    'x': bx + bw // 2,
                    'y': by + bh // 2,
                    'w': bw,
                    'h': bh,
                }
                # Ищем иконку
                for icon_child in sub.get('children', []) or []:
                    if icon_child.get('pythonObjectTypeName') == 'Icon':
                        icon_data = icon_child.get('dictEntriesOfInterest', {})
                        icon = str(icon_data.get('_texturePath', ''))

            module_info = {
                'name': slot_name,
                'active': is_active,
                'icon': icon,
            }
            if btn:
                module_info.update(btn)

            result['modules'][slot_name] = 'ACTIVE' if is_active else 'READY'

            if slot_type == 'high':
                result['high_slots'].append(module_info)
            elif slot_type == 'medium':
                result['medium_slots'].append(module_info)
            elif slot_type == 'low':
                result['low_slots'].append(module_info)
            return   # слоты не вкладываются друг в друга

        for child in node.get('children', []) or []:
            walk(child, ax, ay)

    walk(root, 0, 0)
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

    Полосы HP (для детекта урона дронов):
      Fill(_name='droneGaugeBar', _displayWidth=32)   — полная полоса HP
      Fill(_name='droneGaugeBarDmg', _displayWidth=0) — полоса урона (0 = нет урона)
    bar_width/damage_width кладём в result — бот собирает дронов при damage/bar > 0.5.
    """
    result = {
        'name': '',
        'status': 'UNKNOWN',
        'hp': 100.0,
        'under_attack': False,
        'bar_width': 0,
        'damage_width': 0,
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

        # Полосы HP: полная и урона (для детекта урона дрона по 50% порогу)
        if t == 'Fill':
            nm = str(ei.get('_name', '') or '')
            if nm in ('droneGaugeBar', 'droneGaugeBarDmg'):
                w = get_val(ei.get('_displayWidth'))
                if nm == 'droneGaugeBar':
                    result['bar_width'] = int(w)
                else:
                    result['damage_width'] = int(w)

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

# Человекочитаемые подписи кнопок окна выбранного объекта (по _name кнопки)
_SELECTED_ITEM_ACTIONS = {
    'selectedItemApproach': 'Approach',
    'selectedItemWarpTo': 'Warp To',
    'selectedItemDock': 'Dock',
    'selectedItemOrbit': 'Orbit',
    'selectedItemKeepAtRange': 'Keep at Range',
    'selectedItemLockTarget': 'Lock Target',
    'selectedItemUnLockTarget': 'Unlock Target',
    'selectedItemLookAt': 'Look At',
    'selectedItemSetInterest': 'Set Interest',
    'selectedItemShowInfo': 'Show Info',
    'selectedItemAccessDropbox': 'Access Dropbox',
}


def parse_selected_item(root: Dict) -> Optional[Dict]:
    """Парсинг выбранного объекта (SelectedItemWnd).

    Реальная структура окна (см. uiroot.json):
      SelectedItemWnd
        +-- Container 'content'
              +-- ContainerAutoSize 'headerParent'  (заголовок "Selected Item")
              +-- Container 'main'
                    +-- ContainerAutoSize 'bottomCont'
                    |     +-- Container 'buttonCont'
                    |           +-- SelectedItemButton 'selectedItem<Действие>' x N
                    +-- Container 'topCont'
                          +-- iconCont (иконка объекта)
                          +-- labelCont -> clipCont -> mainCont -> nameRowCont
                                +-- EveLabelMedium 'nameLabel'     (имя объекта)
                                +-- EveLabelMedium 'distanceLabel' (дистанция)

    Вложенность меняется между версиями клиента, поэтому лейблы ищем
    по _name на любой глубине, а не по фиксированному пути в дереве.
    Дополнительно собираем все кнопки-действия с их экранными координатами.

    Возвращает dict:
      {'name': str, 'distance': float, 'is_target': bool, 'icon': str,
       'x': int, 'y': int, 'w': int, 'h': int,
       'actions': [{'name', 'action', 'x', 'y', 'w', 'h', 'icon'}, ...]}
    или None, если объект не выбран (окно пустое).
    """
    selected_wnd = find_first_by_type(root, 'SelectedItemWnd')
    if not selected_wnd:
        return None

    wei = selected_wnd.get('dictEntriesOfInterest', {}) or {}
    win_x = get_val(wei.get('_displayX', 0))
    win_y = get_val(wei.get('_displayY', 0))

    result: Dict[str, Any] = {
        'name': '',
        'distance': 0.0,
        'is_target': False,
        'icon': '',
        'x': win_x,
        'y': win_y,
        'w': get_val(wei.get('_displayWidth', 0)),
        'h': get_val(wei.get('_displayHeight', 0)),
        'actions': [],
    }

    # 1. Имя и дистанция — лейблы nameLabel/distanceLabel на любой глубине
    for node in find_children_by_type(selected_wnd, 'EveLabelMedium'):
        nei = node.get('dictEntriesOfInterest', {}) or {}
        label_name = str(nei.get('_name', ''))
        text = clean_html(str(nei.get('_setText', nei.get('_text', ''))))
        if not text:
            continue
        if label_name == 'distanceLabel' or _is_distance_label(text):
            result['distance'] = parse_distance(text)
        elif label_name == 'nameLabel' or not result['name']:
            result['name'] = text

    # 2. Иконка объекта (topCont -> iconCont -> Icon 'iconSprite')
    icon_node = find_first_by_type(selected_wnd, 'Icon')
    if icon_node:
        result['icon'] = str(
            (icon_node.get('dictEntriesOfInterest', {}) or {}).get('_texturePath', ''))

    # 3. Кнопки-действия: все SelectedItemButton окна,
    #    координаты — накоплением _displayX/_displayY от окна (абсолютные)
    def walk_buttons(node: Dict, px: int, py: int) -> None:
        ei = node.get('dictEntriesOfInterest', {}) or {}
        ax = px + get_val(ei.get('_displayX', 0))
        ay = py + get_val(ei.get('_displayY', 0))

        if node.get('pythonObjectTypeName') == 'SelectedItemButton':
            btn_name = str(ei.get('_name', ''))
            result['actions'].append({
                'name': btn_name,
                'action': _SELECTED_ITEM_ACTIONS.get(
                    btn_name, btn_name.replace('selectedItem', '')),
                'x': ax,
                'y': ay,
                'w': get_val(ei.get('_displayWidth', 0)),
                'h': get_val(ei.get('_displayHeight', 0)),
                'icon': str(ei.get('texturePath', '')),
            })
            if 'UnLock' in btn_name:
                result['is_target'] = True

        for child in node.get('children', []) or []:
            walk_buttons(child, ax, ay)

    for child in selected_wnd.get('children', []) or []:
        walk_buttons(child, win_x, win_y)

    # Объект не выбран: в окне нет ни имени, ни кнопок действий
    if not result['name'] and not result['actions']:
        return None

    return result


# ==================== ПАРСЕР ОВЕРВЬЮ ====================

def parse_overview(root: Dict) -> List[Dict]:
    """Парсинг овервью.

    Координаты строк (x/y — центр иконки объекта) АБСОЛЮТНЫЕ: накапливаются
    от корня дерева, как в new_parser.parse_npc_lists — бот кликает по ним
    (выбор цели, хоткей W + клик для орбиты).
    """
    result = []

    def walk(node: Dict, px: float, py: float) -> None:
        if not isinstance(node, dict):
            return
        ei = node.get('dictEntriesOfInterest', {}) or {}
        ax = px + get_val(ei.get('_displayX'))
        ay = py + get_val(ei.get('_displayY'))
        if node.get('pythonObjectTypeName') == 'OverviewScrollEntry':
            parsed = parse_overview_entry(node, ax, ay)
            if parsed:
                result.append(parsed)
            return   # записи овервью не вкладываются друг в друга
        for child in node.get('children', []) or []:
            walk(child, ax, ay)

    walk(root, 0, 0)
    return result


def npc_class_from_values(values) -> Optional[str]:
    """Класс NPC по строкам 'npc*' в значениях узла ('npcfrigate' -> 'FRIGATE').

    Используется и в овервью (parse_overview_entry -> npc_class), и в
    классификации NPC иконок (new_parser.parse_npc_lists).
    """
    for val in values:
        v_str = str(val).lower()
        if 'npc' not in v_str:
            continue
        if 'npcfrigate' in v_str:
            return "FRIGATE"
        if 'npcbattleship' in v_str:
            return "BATTLESHIP"
        if 'npcbattlecruiser' in v_str:
            return "BATTLECRUISER"
        if 'npccruiser' in v_str:
            return "CRUISER"
        # Просто 'npc' без уточнения класса
        return "NPC"
    return None


def parse_overview_entry(entry_node: Dict, ox: float = 0.0, oy: float = 0.0) -> Optional[Dict]:
    """Парсинг одной записи овервью.

    ox, oy — накопленные от корня координаты записи; x/y иконки получаются
    абсолютными (центр иконки) — бот кликает по строке (орбита W+клик).
    """
    result = {
        'name': '',
        'distance': 0.0,
        'type': '',
        'x': 0,
        'y': 0,
        'icon': '',
        'npc_class': None,
        'attacking_me': False,
        'threat_hostile': False,
        'hostile': False,
        'targeted_by_me': False,
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
            coords = get_node_coords(child, ox, oy)
            # Центр иконки (как в parse_npc_lists — туда же кликает бот)
            result['x'] = coords['x'] + get_val(ei.get('_displayWidth', 0)) // 2
            result['y'] = coords['y'] + get_val(ei.get('_displayHeight', 0)) // 2
            
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
                    # Класс NPC по имени текстуры ('npcfrigate' -> 'FRIGATE' и т.д.)
                    tex = str(texture).lower()
                    if 'npcfrigate' in tex:
                        result['npc_class'] = 'FRIGATE'
                    elif 'npcbattlecruiser' in tex:
                        result['npc_class'] = 'BATTLECRUISER'
                    elif 'npcbattleship' in tex:
                        result['npc_class'] = 'BATTLESHIP'
                    elif 'npccruiser' in tex:
                        result['npc_class'] = 'CRUISER'
                    elif 'npcdestroyer' in tex:
                        result['npc_class'] = 'DESTROYER'

            # Флаги NPC-состояний: BlinkingSpriteOnSharedCurve (внутри Transform
            # внутри SpaceObjectIcon) — по _name узла
            for node in iter_nodes(child):
                if node.get('pythonObjectTypeName') == 'BlinkingSpriteOnSharedCurve':
                    node_name = (node.get('dictEntriesOfInterest') or {}).get('_name', '')
                    if node_name == 'attackingMe':
                        result['attacking_me'] = True
                    elif node_name == 'threatHostile':
                        result['threat_hostile'] = True
                    elif node_name == 'hostile':
                        result['hostile'] = True
                # Индикатор «взял в цель я» (Sprite targetedByMeIndicator)
                elif node.get('pythonObjectTypeName') == 'Sprite':
                    node_name = (node.get('dictEntriesOfInterest') or {}).get('_name', '')
                    if node_name == 'targetedByMeIndicator':
                        result['targeted_by_me'] = True

    return result if result['name'] else None


# ==================== ПАРСЕР КНОПКИ UNDOCK ====================

def parse_undock_button(root: Dict) -> Optional[Dict]:
    """Координаты кнопки Undock (станция, экран дока).

    Накапливаем _displayX/_displayY от корня (как в parse_modules):
      {'x': центрX, 'y': центрY, 'w': ширина, 'h': высота}
    или None, если кнопки нет в дереве.
    """
    result: Optional[Dict] = None

    def walk(node: Dict, px: float, py: float) -> None:
        nonlocal result
        if not isinstance(node, dict) or result is not None:
            return
        ei = node.get('dictEntriesOfInterest', {}) or {}
        ax = px + get_val(ei.get('_displayX'))
        ay = py + get_val(ei.get('_displayY'))
        if node.get('pythonObjectTypeName') == 'UndockButton':
            w = get_val(ei.get('_displayWidth'))
            h = get_val(ei.get('_displayHeight'))
            result = {'x': ax + w // 2, 'y': ay + h // 2, 'w': w, 'h': h}
            return
        for child in node.get('children', []) or []:
            walk(child, ax, ay)

    walk(root, 0, 0)
    return result


# ==================== ПАРСЕР ПРОБ-СКАНЕРА ====================

def parse_scan_results(root: Dict) -> List[Dict]:
    """Парсинг результатов проб-сканера (окно Probe Scanner).

    Вместо хрупкого пути по именам контейнеров ищем ВСЕ узлы
    ScanResultNew внутри ProbeScannerWindow. Реальное расположение
    (свежий дамп): content -> main -> ProbeScannerPalette ->
    Container('ScanResultsContainer') -> ScanResults('resultScroll') ->
    Container('maincontainer') -> Container('__clipper') ->
    Container('__content') -> ScanResultNew*; при пересканировании строки
    могут временно оказаться в SwipeAnimator('scanAnimator').

    Координаты строки (x, y, h) — АБСОЛЮТНЫЕ (накоплением от окна сканера):
    бот кликает по ним, чтобы открыть контекстное меню аномалии.
    """
    result: List[Dict] = []
    scanner_wnd = find_first_by_type(root, 'ProbeScannerWindow')
    if not scanner_wnd:
        return result

    def collect(node, px, py):
        if not isinstance(node, dict):
            return
        ei = node.get('dictEntriesOfInterest', {}) or {}
        ax = px + get_val(ei.get('_displayX', 0))
        ay = py + get_val(ei.get('_displayY', 0))

        if node.get('pythonObjectTypeName') == 'ScanResultNew':
            parsed = parse_scan_result(node)
            if parsed:
                parsed['x'] = ax
                parsed['y'] = ay
                parsed['h'] = get_val(ei.get('_displayHeight', 0))
                result.append(parsed)
            return  # вложенных ScanResultNew не бывает
        for child in node.get('children', []) or []:
            collect(child, ax, ay)

    collect(scanner_wnd, 0, 0)
    return result


# Известные названия групп сайтов в колонке Group окна Probe Scanner
_SITE_GROUP_NAMES = {
    'ore site', 'combat site', 'cosmic signature', 'cosmic anomaly',
    'gas site', 'data site', 'relic site', 'wormhole',
}

# ID сигнатуры: 'ZYY-853' (3 буквы + '-' + 3 цифры)
_SIG_ID_RE = re.compile(r'^[A-Z]{3}-\d{3}$')
_PCT_RE = re.compile(r'^[\d.,]+\s*%$')
_DIST_RE = re.compile(r'^[\d.,]+\s*(au|km|m)$', re.IGNORECASE)


def parse_scan_result(scan_node: Dict) -> Optional[Dict]:
    """Парсинг одной строки результата сканирования (ScanResultNew).

    Реальная структура строки (свежий дамп ProbeScanner):
      ScanResultNew(_name='scan_result_ZYY-853')
        Container > Container > EveLabelMedium('0,0%')        # сила сигнала
        Container > Container > [Container(Sprite(иконка типа сайта)),
                                 Container(EveLabelMedium('19,59 AU')),  # дистанция
                                 Container(EveLabelMedium('ZYY-853')),   # ID
                                 Container(EveLabelMedium('Cosmic Signature')),  # группа
                                 Container(EveLabelMedium(имя сайта/пусто))]
        BarGraphBarHorizontal(name='statusBar')

    Метки лежат на разной глубине, поэтому собираем ВСЕ тексты рекурсивно
    и классифицируем их по паттернам; ID дополнительно берём из имени узла
    ('scan_result_XXX-123'), тип сайта — из текстуры иконки.
    """
    result: Dict[str, Any] = {
        'id': '',
        'name': '',
        'group': '',
        'distance': 0.0,
        'signal': 0.0,
        'type': 'signature',
    }

    # ID надёжнее всего закодирован в имени узла: 'scan_result_XXX-123'
    node_name = str((scan_node.get('dictEntriesOfInterest') or {}).get('_name', ''))
    if node_name.startswith('scan_result_'):
        result['id'] = node_name[len('scan_result_'):]

    texts: List[str] = []

    def collect(node):
        if not isinstance(node, dict):
            return
        t = node.get('pythonObjectTypeName', '')
        ei = node.get('dictEntriesOfInterest', {}) or {}
        if t in ('EveLabelMedium', 'EveLabelSmall', 'EveLabelLarge', 'Label'):
            text = clean_html(get_node_text(node))
            if text:
                texts.append(text)
        elif t == 'Sprite':
            tex = str(ei.get('_texturePath', ''))
            if 'ore_site' in tex:
                result['type'] = 'ore'
            elif 'combatSite' in tex:
                result['type'] = 'combat'
            elif 'relicSite' in tex:
                result['type'] = 'relic'
            elif 'dataSite' in tex:
                result['type'] = 'data'
            elif 'gasSite' in tex:
                result['type'] = 'gas'
            elif 'wormhole' in tex:
                result['type'] = 'wormhole'
            elif 'scanResultLocation' in tex:
                result['type'] = 'signature'
        for child in node.get('children', []) or []:
            collect(child)

    collect(scan_node)

    for text in texts:
        if _PCT_RE.match(text):
            result['signal'] = parse_percent(text)
        elif _DIST_RE.match(text):
            result['distance'] = parse_distance(text)
        elif _SIG_ID_RE.match(text):
            result['id'] = text
        elif text.lower() in _SITE_GROUP_NAMES:
            result['group'] = text
        elif not result['name']:
            result['name'] = text

    return result if (result['id'] or result['name']) else None


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


# ==================== ИЗ new_parser.py: КОНСТАНТЫ И ХЕЛПЕРЫ ====================

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

def iter_nodes(node):
    """Генератор: все узлы дерева (dict) в порядке обхода."""
    if isinstance(node, dict):
        yield node
        for child in node.get('children', []) or []:
            yield from iter_nodes(child)



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

# ==================== КЛАССИФИКАЦИЯ NPC (из овервью) ====================
# Перенесено из старого EveParser (_check_overview): ищем SpaceObjectIcon и
# определяем класс NPC по строкам 'npc*' в значениях узла и его прямых детей.

def _npc_ship_type(values) -> Optional[str]:
    """Класс NPC по значениям узла ('npcfrigate' -> FRIGATE). None = не NPC.

    Логика общая с full_parser.parse_overview_entry (см. npc_class_from_values).
    """
    return npc_class_from_values(values)


def parse_npc_lists(root: Dict) -> Dict[str, Any]:
    """Сбор всех NPC из овервью с классификацией и АБСОЛЮТНЫМИ координатами.

    Возвращает:
      {
        'all':            [(x, y), ...],   # центры иконок, координаты окна игры
        'frigates':       [(x, y), ...],
        'cruisers':       [(x, y), ...],
        'battlecruisers': [(x, y), ...],
        'battleships':    [(x, y), ...],
        'draw':           [{'rect': (x, y, w, h), 'color': '#ff3333',
                            'priority': 15, 'label': 'FRIGATE'}, ...]
      }
    """
    result: Dict[str, Any] = {
        'all': [], 'frigates': [], 'cruisers': [],
        'battlecruisers': [], 'battleships': [], 'draw': [],
    }

    def walk(node: Dict, px: float, py: float) -> None:
        if not isinstance(node, dict):
            return
        ei = node.get('dictEntriesOfInterest', {}) or {}
        lx, ly = get_val(ei.get('_displayX')), get_val(ei.get('_displayY'))
        w, h = get_val(ei.get('_displayWidth')), get_val(ei.get('_displayHeight'))
        ax, ay = px + lx, py + ly

        if node.get('pythonObjectTypeName') == 'SpaceObjectIcon':
            # Сканируем данные узла и его прямых детей на наличие 'npc'
            values = list(ei.values())
            for child in node.get('children', []) or []:
                cei = child.get('dictEntriesOfInterest', {}) or {}
                values.extend(cei.values())

            ship_type = _npc_ship_type(values)
            if ship_type:
                coords = (ax + w / 2, ay + h / 2)
                result['all'].append(coords)
                if ship_type == "FRIGATE":
                    result['frigates'].append(coords)
                elif ship_type == "BATTLESHIP":
                    result['battleships'].append(coords)
                elif ship_type == "CRUISER":
                    result['cruisers'].append(coords)
                elif ship_type == "BATTLECRUISER":
                    result['battlecruisers'].append(coords)
                # Рамка NPC для Debug HUD (как в старом парсере)
                result['draw'].append({
                    'rect': (ax, ay, w, h),
                    'color': '#ff3333',
                    'priority': 15,
                    'label': ship_type,
                })

        for child in node.get('children', []) or []:
            walk(child, ax, ay)

    walk(root, 0, 0)
    return result

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
    add('title', "EVE PARSER DEBUG")
    dim(f"{'=' * 74}")
    dim(f"updated: {ts}  window: {state.window_width}x{state.window_height}  fps: {state.fps if state.fps else '-'}")

    # ===== 0. МОДАЛЬНЫЕ ОКНА (blocking popup) =====
    modal = getattr(state, 'modal_info', None) or {}
    if modal.get('open'):
        add('danger', f"  MODAL BLOCKING: {modal.get('name') or '?'} "
                      f"@ ({modal.get('x')},{modal.get('y')}) "
                      f"close={bool(modal.get('close'))}")
    else:
        dim("  (no modal blocking window)")

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


# ==================== ГЛАВНЫЙ ПАРСЕР ====================

def _finalize_state(state: ParsedUIState) -> None:
    """До-расчёт полей, которые full_parser не заполняет:
    - repairers: модули-ремонтники по иконке (как в старом парсере);
    - prop_modules: двигатель (afterburner/MWD) по иконке;
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

    # 1b. Двигатель (afterburner/MWD) — по иконке модуля.
    #     3_64_2 — иконка доп.движения (AB/MWD) в HUD, 1_64_8 — вариант
    #     из других скинов/клиентов. Бот держит двигатель всегда включённым.
    prop_icons = ('3_64_2', '1_64_8')
    prop_modules: Dict[str, str] = {}
    for slot_list in (state.high_slots, state.medium_slots, state.low_slots):
        for m in slot_list or []:
            icon = str(m.get('icon') or '').lower()
            if not icon:
                continue
            if (any(p in icon for p in prop_icons)
                    or 'afterburner' in icon or 'microwarp' in icon):
                slot_name = m.get('name', '')
                if slot_name:
                    prop_modules[slot_name] = 'ACTIVE' if m.get('active') else 'READY'
    state.prop_modules = prop_modules

    # 2. Боевой режим (отображается в окне отладки, боту не передаётся)
    if state.local_has_hostile or state.local_has_neutral:
        state.combat_mode_active = "SAFETY"
    elif state.drones_fighting or state.drones_in_space > 0:
        state.combat_mode_active = "DRONES"
    elif state.targets or state.npc_all:
        state.combat_mode_active = "GUNS"
    else:
        state.combat_mode_active = "IDLE"


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

    # ====== ASTEROID BELTS (из открытого контекстного меню) ======
    state.asteroid_belts = parse_asteroid_belts(root)
    
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
    
    # ====== ЛОКАЛЬНЫЙ ЧАТ (точная версия) ======
    chat_data = parse_local_chat_new(root)
    state.local_player_count = chat_data['player_count']
    state.local_has_neutral = chat_data['has_neutral']
    state.local_has_hostile = chat_data['has_hostile']
    state.local_players = chat_data['players']
    
    # ====== ПРОБ-СКАНЕР ======
    state.scan_results = parse_scan_results(root)
    # Окно Probe Scanner открыто? (если нет — бот нажмёт Alt+P)
    state.probe_scanner_open = find_first_by_type(root, 'ProbeScannerWindow') is not None
    # Кнопка Undock (станция) — для автоматического андока
    state.undock_button = parse_undock_button(root)
    
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
    
    # ====== ЭКРАН ЗАГРУЗКИ ======
    # Надёжный признак: в дереве нет ни космического UI (ShipUI), ни ангарных
    # элементов (UndockButton/HangarLayer). Прежняя эвристика по цвету первого
    # попавшегося 'Fill' срабатывала на невидимые Fill обычного UI и ВСЕГДА
    # давала is_loading=True — из-за этого миссия бота не стартовала.
    in_space_ui = find_first_by_type(root, 'ShipUI') is not None
    docked_ui = (find_first_by_type(root, 'UndockButton') is not None
                 or find_first_by_type(root, 'HangarLayer') is not None)
    state.is_loading = not (in_space_ui or docked_ui)
    
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

    # ====== NPC (точная классификация из овервью) ======
    npc = parse_npc_lists(root)
    state.npc_all = npc['all']
    state.npc_frigate = npc['frigates']
    state.npc_cruiser = npc['cruisers']
    state.npc_battlecruiser = npc['battlecruisers']
    state.npc_battleship = npc['battleships']

    # ====== МОДАЛКИ ======
    state.modal_info = parse_modal_info(root)

    # ====== ФИНАЛИЗАЦИЯ (репки/двигатель/режим) ======
    _finalize_state(state)

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

def _build_draw_list(state) -> List[Dict[str, Any]]:
    """Прямоугольники для debug_overlay (NPC + targets + пилоты локала)."""
    draw: List[Dict[str, Any]] = []
    for ship_type, lst in (("FRIGATE", state.npc_frigate),
                           ("CRUISER", state.npc_cruiser),
                           ("BATTLECRUISER", state.npc_battlecruiser),
                           ("BATTLESHIP", state.npc_battleship)):
        for (nx, ny) in lst or []:
            draw.append({'rect': (nx - 8, ny - 8, 16, 16),
                         'color': '#ff3333', 'priority': 15, 'label': ship_type})
    for t in state.targets or []:
        draw.append({'rect': (t.get('x', 0), t.get('y', 0), 64, 18),
                     'color': '#ffffff', 'priority': 10, 'label': 'TARGET'})
    for p in state.local_players or []:
        st = p.get('standing') or STANDING_UNKNOWN
        draw.append({'rect': (p.get('x', 0), p.get('y', 0), 18, 16),
                     'color': STANDING_COLORS.get(st, STANDING_COLORS[STANDING_UNKNOWN]),
                     'priority': 12, 'label': p.get('name', '')})
    return draw


class EveParserNew:
    """Тонкая обёртка: parse(json) -> (state, draw_list)."""

    def __init__(self):
        self.last_draw_list: List[Dict[str, Any]] = []

    def parse(self, json_data: Dict) -> Tuple[ParsedUIState, List[Dict[str, Any]]]:
        state = parse_ui_root(json_data)
        draw_list = _build_draw_list(state)
        self.last_draw_list = draw_list
        return state, draw_list


if __name__ == "__main__":
    main()