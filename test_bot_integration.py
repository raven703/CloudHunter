# -*- coding: utf-8 -*-
"""Проверка bot.EveBot.think() на состоянии нового парсера (без pyautogui/pygetwindow).

Покрывает: SAFETY-выход, захват целей (фрегаты первыми), focus fire,
цикл выживания дронов, статистику сессии (киллы/потери дронов/побеги).
"""
import io
import json
import sys
import time
from unittest.mock import MagicMock

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Заглушки оконных/вводных библиотек (в этом venv их нет; у пользователя стоят)
for mod in ("pyautogui", "pygetwindow"):
    if mod not in sys.modules:
        stub = MagicMock()
        if mod == "pygetwindow":
            stub.getWindowsWithTitle.return_value = []
        sys.modules[mod] = stub

# Ускоряем тест: убираем реальные sleep (monotonic/time остаются живыми)
_orig_sleep = time.sleep
time.sleep = lambda *_: None

from bot import EveBot
from new_parser import EveParserNew
from ParsedUIState import ParsedUIState

import os
uiroot_path = "uiroot_new.json"
if os.path.exists(uiroot_path):
    # Живой дамп (генерится eveui.exe во время игровой сессии)
    with open(uiroot_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    state, draw_list = EveParserNew().parse(data)
else:
    # Дампа нет — синтетическое пустое состояние; секции ниже сами выставляют поля
    state, draw_list = ParsedUIState(), []

# Репку делаем включённой (ACTIVE), чтобы _check_and_repair не жал F1
state.repairers = {'inFlightLowSlot3': 'ACTIVE'}
# Секция 1: враг в локале -> аварийный выход (не зависим от содержимого дампа)
state.local_has_hostile = True

stats = {'sites_cleared': 0, 'kills': 0, 'drones_lost': 0, 'escapes': 0}
bot = EveBot(stats=stats)
bot._activate_game_window = lambda: True   # окна игры нет — не выходим из think()
pag = sys.modules["pyautogui"]


def reset_mock():
    pag.reset_mock(return_value=False, side_effect=True)


def pressed_keys():
    """Список клавиш, переданных в pyautogui.press()."""
    return [c.args[0] for c in pag.press.call_args_list if c.args]

# ============ 1. HOSTILE в локале -> аварийный выход ============
calls = []
_orig_exit = bot.emergency_exit
bot.emergency_exit = lambda reason: (calls.append(reason), _orig_exit(reason))
bot.think(state, use_drones=True)
print(f"1) hostile in local  -> emergency_exit: {calls}, mode={state.combat_mode_active}, "
      f"escapes={stats['escapes']}")
assert calls and state.combat_mode_active == "SAFETY"
assert stats['escapes'] == 1

# ============ 2. FSM цели: выбор -> орбита -> лок -> атака ============
state.local_has_hostile = False
state.local_has_neutral = False
state.targets = []
state.active_target = None
state.npc_all = [(620.0, 410.0), (700.0, 420.0)]
state.npc_frigate = [(620.0, 410.0)]
state.drones_in_space = 0
state.drones_in_bay = 9
state.selected_item = {'name': 'Rat Frigate', 'distance': 25000.0, 'actions': [
    {'name': 'selectedItemApproach', 'action': 'Approach', 'x': 1251, 'y': 98, 'w': 30, 'h': 30},
    {'name': 'selectedItemOrbit', 'action': 'Orbit', 'x': 1395, 'y': 98, 'w': 30, 'h': 30},
]}
calls.clear()

# 2a: выбор цели — одиночный клик по овервью (без ctrl)
reset_mock()
bot.think(state, use_drones=True)
kd = [c.args for c in pag.keyDown.call_args_list if c.args]
print(f"2a) select target    -> step={bot._combat_step}, clicks={pag.click.call_count}, ctrl={kd}")
assert not calls, "safe local must NOT trigger emergency exit"
assert state.combat_mode_active == "COMBAT"
assert bot._combat_step == 'SELECT'
assert pag.click.call_count == 1 and not kd, "select: single click without ctrl"

# 2b: орбита выбранной цели (кнопка Orbit окна выбранного объекта)
bot._step_ts -= 10
reset_mock()
bot.think(state, use_drones=True)
moves = [c.args for c in pag.moveTo.call_args_list if c.args]
print(f"2b) orbit action     -> step={bot._combat_step}, moves={moves}")
assert bot._combat_step == 'ORBIT'
assert any(1410 <= m[0] <= 1445 and 110 <= m[1] <= 116 for m in moves), \
    f"orbit: must click Orbit button at ~(1410..1445, 113), got {moves}"

# 2c: попытка захвата в лок — Ctrl+клик
bot._step_ts -= 10
reset_mock()
bot.think(state, use_drones=True)
kd = [c.args for c in pag.keyDown.call_args_list if c.args]
print(f"2c) lock attempt     -> step={bot._combat_step}, ctrl={kd}, attempts={bot._lock_attempts}")
assert bot._combat_step == 'LOCK' and bot._lock_attempts == 1
assert kd and kd[0] == ('ctrl',), f"lock: ctrl must be held, got {kd}"

# 2d: лок не сработал (цель вне зоны захвата) -> сближение Approach
bot._step_ts -= 10
reset_mock()
bot.think(state, use_drones=True)
moves = [c.args for c in pag.moveTo.call_args_list if c.args]
print(f"2d) out of lock range-> step={bot._combat_step}, moves={moves}")
assert bot._combat_step == 'APPROACH'
assert any(1265 <= m[0] <= 1300 and 110 <= m[1] <= 116 for m in moves), \
    f"approach: must click Approach button at ~(1265..1300, 113), got {moves}"

# 2e: цель захвачена -> ATTACK + автозапуск оружия (F1 для неактивного слота)
state.targets = [{'name': 'Rat', 'distance': 25000.0, 'is_active': True, 'x': 100, 'y': 100}]
state.active_target = state.targets[0]
state.high_slots = [{'name': 'inFlightHighSlot1', 'active': False},
                    {'name': 'inFlightHighSlot2', 'active': True}]
reset_mock()
bot.think(state, use_drones=True)
print(f"2e) locked           -> step={bot._combat_step}, pressed={pressed_keys()}")
assert bot._combat_step == 'ATTACK'
assert 'f1' in pressed_keys(), "auto_attack must press F1 for inactive gun slot"

# ============ 3. Целей нет, дроны в космосе -> сбор по таймеру ============
state.npc_all = []
state.npc_frigate = []
state.drones_in_space = 2
state.drones_in_bay = 0
bot.no_targets_timer = time.time() - 11.0
reset_mock()
bot.think(state, use_drones=True)
print(f"3) no targets, drones in space -> recall: pressed={pressed_keys()}, "
      f"mode={state.combat_mode_active}")
assert 'r' in pressed_keys()

# ============ 4. Цикл выживания дронов ============
state.npc_all = [(620.0, 410.0)]
state.npc_frigate = [(620.0, 410.0)]
state.targets = [{'name': 'Rat', 'distance': 1000.0, 'is_active': True, 'x': 100, 'y': 100}]
state.active_target = state.targets[0]
state.drones_in_space = 2
state.drones_in_bay = 0
state.drones_under_attack = True
state.drones_fighting = True
bot._drone_baseline = None   # сброс базы дронов (сценарии выше меняли их число)
# 4а. Дронов бьют -> Shift+R, фаза RECALLING
bot._drone_phase = 'FIGHTING'
bot._last_lock_action_ts = time.monotonic()   # блокируем клики по целям
reset_mock()
bot.think(state, use_drones=True)
print(f"4a) drones under attack -> recall: pressed={pressed_keys()}, phase={bot._drone_phase}")
assert 'r' in pressed_keys() and bot._drone_phase == 'RECALLING'

# 4б. Все дроны собрались -> COOLDOWN (без команд отзыва)
state.drones_in_space = 0
state.drones_in_bay = 2
state.drones_under_attack = False
bot.cfg['drone_relaunch_delay'] = 0.0
bot._last_lock_action_ts = time.monotonic()
reset_mock()
bot.think(state, use_drones=True)
print(f"4b) drones collected -> phase={bot._drone_phase}, pressed={pressed_keys()}")
assert bot._drone_phase in ('COOLDOWN', 'FIGHTING') and 'r' not in pressed_keys()

# 4в. Кулдаун прошёл: тик 1 — переход в FIGHTING, тик 2 — выпуск (Shift+F)
reset_mock()
bot.think(state, use_drones=True)   # COOLDOWN -> FIGHTING
bot.think(state, use_drones=True)   # FIGHTING -> выпуск дронов
print(f"4c) relaunch -> pressed={pressed_keys()}, phase={bot._drone_phase}")
assert 'f' in pressed_keys() and bot._drone_phase == 'FIGHTING'

# ============ 5. Focus fire: приоритет фрегата, даже если он дальше ============
state.overview_entries = [
    {'name': 'Rat Frigate', 'distance': 12000.0, 'type': '', 'x': 0, 'y': 0,
     'icon': '', 'npc_class': 'FRIGATE'},
    {'name': 'Rat Battleship', 'distance': 8000.0, 'type': '', 'x': 0, 'y': 0,
     'icon': '', 'npc_class': 'BATTLESHIP'},
]
t_battle = {'name': '4GQ-XQ - Rat Battleship', 'distance': 8000.0, 'is_active': True,
            'x': 100, 'y': 100}
t_frigate = {'name': '4GQ-XQ - Rat Frigate', 'distance': 12000.0, 'is_active': False,
             'x': 100, 'y': 130}
state.targets = [t_battle, t_frigate]
state.active_target = t_battle   # активна цель-линкор, а фрегат опаснее
state.drones_in_bay = 2
state.drones_in_space = 0
bot._last_lock_action_ts = 0.0
reset_mock()
bot.think(state, use_drones=True)
moves = [c.args for c in pag.moveTo.call_args_list if c.args]
print(f"5) focus fire: active=battleship, frigate priority -> moves={moves}")
assert moves, "focus switch must click the priority row"
# y клика должен быть у строки фрегата (~130), а не линкора (~100)
ys = [m[1] for m in moves]
assert any(126 <= y <= 136 for y in ys), f"click must target frigate row, got {ys}"

# ============ 6. Статистика: киллы и потеря дронов ============
bot._prev_locked_count = 2
state.targets = [t_battle]   # одна цель умерла
state.npc_all = [(620.0, 410.0)]
bot._track_kills(state)
assert stats['kills'] == 1, f"kills must be 1, got {stats['kills']}"
bot._drone_baseline = 2
state.drones_in_space = 1
state.drones_in_bay = 0
bot._track_drones_state(state)
assert stats['drones_lost'] == 1, f"drones_lost must be 1, got {stats['drones_lost']}"
print(f"6) stats: kills={stats['kills']}, drones_lost={stats['drones_lost']}, "
      f"sites={stats['sites_cleared']}, escapes={stats['escapes']}")

# ============ 7. Миссия: выбор боевой аномалии из сканера (ПКМ) ============
state.npc_all = []
state.npc_frigate = []
state.targets = []
state.active_target = None
state.is_loading = False   # в дампе is_loading=True — для миссии снимаем
state.drones_in_space = 0
state.drones_in_bay = 2
state.scan_results = [
    {'id': 'DPT-665', 'name': 'Sansha Den', 'group': 'Combat Site', 'type': 'combat',
     'signal': 0.0, 'x': 500, 'y': 300, 'h': 24},
    {'id': 'JXT-320', 'name': 'Medium Arkonor', 'group': 'Ore Site', 'type': 'ore',
     'signal': 0.0, 'x': 500, 'y': 330, 'h': 24},   # не боевая — фильтруется
]
reset_mock()
bot.think(state, use_drones=True)
right_clicks = [c for c in pag.click.call_args_list
                if c.kwargs.get('button') == 'right']
print(f"7) anomaly pick      -> mission={bot._mission}, right_clicks={len(right_clicks)}, "
      f"current={bot._current_anom}")
assert bot._mission == 'MENU_WAIT'
assert len(right_clicks) == 1, "mission must right-click the combat anomaly row"
assert bot._current_anom == 'Sansha Den'
# координаты ПКМ видны в moveTo: строка аномалии ~(500+40, 300+12) + рандом
moves = [c.args for c in pag.moveTo.call_args_list if c.args]
assert any(540 <= m[0] <= 565 and 308 <= m[1] <= 316 for m in moves), \
    f"right-click must be on the anomaly row (~543..560, 310..314), got {moves}"

# ============ 8. Контекстное меню: клик 'Warp to Location' ============
state.menu_open = True
state.menu_items = [
    {'type': 'entry', 'text': 'Approach', 'x': 520, 'y': 324, 'w': 120, 'h': 16,
     'has_submenu': False, 'name': '', 'icon': None},
    {'type': 'entry', 'text': 'Warp to Location', 'x': 520, 'y': 340, 'w': 120, 'h': 16,
     'has_submenu': True, 'name': '', 'icon': None},
]
reset_mock()
# Меню «уже стабилизировалось» — сдвигаем таймер стабилизации в прошлое
bot._mission_ts = time.time() - bot.cfg['menu_settle'] - 0.3
bot.think(state, use_drones=True)
print(f"8) menu: warp item   -> mission={bot._mission}")
assert bot._mission == 'SUBMENU_WAIT'
left_clicks = [c for c in pag.click.call_args_list
               if c.kwargs.get('button') != 'right']
assert len(left_clicks) == 1, "must click the 'Warp to Location' entry once"

# ============ 9. Подменю дистанций: клик 'within 20 km' ============
state.menu_items = [
    {'type': 'entry', 'text': 'within 0 m', 'x': 640, 'y': 340, 'w': 100, 'h': 16,
     'has_submenu': False, 'name': '', 'icon': None},
    {'type': 'entry', 'text': 'within 20 km', 'x': 640, 'y': 388, 'w': 100, 'h': 16,
     'has_submenu': False, 'name': '', 'icon': None},
]
reset_mock()
# Подменю «уже появилось и стабилизировалось» — кликаем сразу, в центр строки
bot._submenu_seen_ts = time.time() - bot.cfg['submenu_settle'] - 0.3
bot.think(state, use_drones=True)
moves = [c.args for c in pag.moveTo.call_args_list if c.args]
print(f"9) submenu distance  -> mission={bot._mission}, moves={moves}")
assert bot._mission == 'WARP_CLICKED', f"expected WARP_CLICKED, got {bot._mission}"
# центр 'within 20 km' = (640+50, 388+8) = (690, 396) + рандом _click_at
assert any(645 <= m[0] <= 760 and 390 <= m[1] <= 400 for m in moves), \
    f"must click 'within 20 km' at ~(690, 396), got {moves}"

# ============ 10. Варп подтверждён по HUD -> визит зафиксирован ============
state.is_warp = True
reset_mock()
bot.think(state, use_drones=True)
print(f"10) warp confirmed   -> mission={bot._mission}, visited={bot._current_anom}")
assert bot._mission == 'IDLE' and bot._current_anom is None
assert 'Sansha Den' in bot._visited_anoms

# 10b: сразу после варпа — settle-пауза (овервью прогружается), вылета нет
state.is_warp = False
state.scan_results = [
    {'id': 'AWF-224', 'name': 'Sansha Den 2', 'group': 'Combat Site', 'type': 'combat',
     'signal': 0.0, 'x': 500, 'y': 340, 'h': 24},
]
reset_mock()
bot.think(state, use_drones=True)
right_clicks = [c for c in pag.click.call_args_list
                if c.kwargs.get('button') == 'right']
print(f"10b) settle after warp-> right_clicks={len(right_clicks)}")
assert not right_clicks, "arrival settle must block immediate re-warp"

# 10c: settle прошёл, 'Sansha Den' посещена — летит на следующую аномалию
bot._arrival_ts -= bot.cfg['arrival_settle_time'] + 1
reset_mock()
bot.think(state, use_drones=True)
right_clicks = [c for c in pag.click.call_args_list
                if c.kwargs.get('button') == 'right']
print(f"10c) next anomaly    -> mission={bot._mission}, right_clicks={len(right_clicks)}, "
      f"current={bot._current_anom}")
assert bot._mission == 'MENU_WAIT' and bot._current_anom == 'Sansha Den 2'
assert len(right_clicks) == 1, "after settle the bot must warp to the next anomaly"

# 10d: revisit-cooldown: посещённая аномалия пропускается
bot._visited_anoms['Sansha Den 2'] = time.time()
bot._mission = 'IDLE'
bot._warp_attempts = 0
bot._current_anom = None
reset_mock()
bot.think(state, use_drones=True)
right_clicks = [c for c in pag.click.call_args_list
                if c.kwargs.get('button') == 'right']
print(f"10d) revisit blocked -> right_clicks={len(right_clicks)}")
assert not right_clicks, "visited anomaly must be skipped"

# ============ 11. Выбор warp-пункта по живому меню (из raw_dump) ============
live_menu = [
    {'type': 'entry', 'text': 'Within 0 m', 'x': 837, 'y': 119, 'w': 104, 'h': 22, 'has_submenu': False},
    {'type': 'entry', 'text': 'Within 20 km', 'x': 837, 'y': 163, 'w': 104, 'h': 22, 'has_submenu': False},
    {'type': 'entry', 'text': 'Set Default', 'x': 837, 'y': 286, 'w': 104, 'h': 22, 'has_submenu': True},
    {'type': 'entry', 'text': 'Warp to Within 0 m', 'x': 664, 'y': 97, 'w': 163, 'h': 22, 'has_submenu': False},
    {'type': 'entry', 'text': 'Warp to Within', 'x': 664, 'y': 119, 'w': 163, 'h': 22, 'has_submenu': True},
    {'type': 'entry', 'text': 'Align to', 'x': 664, 'y': 141, 'w': 163, 'h': 22, 'has_submenu': False},
]
state.menu_items = live_menu
picked = bot._pick_warp_menu_item(state)
print(f"11) live menu pick   -> {picked.get('text') if picked else None}")
assert picked is not None and picked.get('text') == 'Warp to Within', \
    "must prefer submenu entry over direct 'Warp to Within 0 m'"

# 11b: только прямой варп на 0 м — не выбираем (опасно в боевой аномалии)
state.menu_items = [
    {'type': 'entry', 'text': 'Warp to Within 0 m', 'x': 664, 'y': 97, 'w': 163, 'h': 22, 'has_submenu': False},
    {'type': 'entry', 'text': 'Align to', 'x': 664, 'y': 141, 'w': 163, 'h': 22, 'has_submenu': False},
]
picked = bot._pick_warp_menu_item(state)
print(f"11b) only 0m warp    -> {picked}")
assert picked is None, "must NOT warp to 0 m in combat anomaly"

# 11c: прямой пункт нужной дистанции — выбирается, если подменю нет
state.menu_items = [
    {'type': 'entry', 'text': 'Warp to Within 0 m', 'x': 664, 'y': 97, 'w': 163, 'h': 22, 'has_submenu': False},
    {'type': 'entry', 'text': 'Warp to Within 20 km', 'x': 664, 'y': 119, 'w': 163, 'h': 22, 'has_submenu': False},
]
picked = bot._pick_warp_menu_item(state)
print(f"11c) direct 20 km    -> {picked.get('text') if picked else None}")
assert picked is not None and picked.get('text') == 'Warp to Within 20 km'

# ============ 12. Живой дамп с открытым меню варпа ============
# Живой дамп raw_dump_20260902_153738.json больше не в репозитории — собираем
# эквивалентное дерево UI инлайн (та же структура, что у настоящих дампов EVE:
# l_menu -> ContextMenu/ContextSubMenu -> mainCont -> MenuEntryView -> TextBody).
def _menu_entry(text, rx, ry, w, h, submenu=False):
    node = {
        'pythonObjectTypeName': 'MenuEntryView',
        'dictEntriesOfInterest': {
            '_name': f'context_menu_{text}',
            '_displayX': rx, '_displayY': ry,
            '_displayWidth': w, '_displayHeight': h,
        },
        'children': [
            {'pythonObjectTypeName': 'TextBody',
             'dictEntriesOfInterest': {'_setText': text},
             'children': []},
        ],
    }
    if submenu:
        node['children'].append({
            'pythonObjectTypeName': 'Sprite',
            'dictEntriesOfInterest': {'_texturePath': 'res:/UI/Texture/WindowMenus/1_16_14.png'},
            'children': [],
        })
    return node


def _menu_window(obj_type, wx, wy, entries):
    return {
        'pythonObjectTypeName': obj_type,
        'dictEntriesOfInterest': {'_displayX': wx, '_displayY': wy},
        'children': [{
            'pythonObjectTypeName': 'ContainerAutoSize',
            'dictEntriesOfInterest': {'_name': 'mainCont', '_displayX': 4, '_displayY': 4},
            'children': entries,
        }],
    }


live = {
    'pythonObjectTypeName': 'UIRoot',
    'dictEntriesOfInterest': {'_name': 'Desktop', '_displayX': 0, '_displayY': 0},
    'children': [{
        'pythonObjectTypeName': 'LayerCore',
        'dictEntriesOfInterest': {'_name': 'l_menu', '_displayX': 0, '_displayY': 0},
        'children': [
            # Основное меню: 'Warp to Within 0 m' @ (664,97), 'Warp to Within' @ (664,119)
            _menu_window('ContextMenu', 660, 93, [
                _menu_entry('Warp to Within 0 m', 0, 0, 163, 22),
                _menu_entry('Warp to Within', 0, 22, 163, 22, submenu=True),
                _menu_entry('Align to', 0, 44, 163, 22),
            ]),
            # Раскрытое подменю дистанций: 'Within 70 km' @ (837,229)
            _menu_window('ContextSubMenu', 833, 71, [
                _menu_entry(f'Within {d}', 0, i * 22, 104, 22)
                for i, d in enumerate(['0 m', '10 km', '20 km', '30 km',
                                       '40 km', '50 km', '60 km', '70 km'])
            ]),
        ],
    }],
}
live_state, _ = EveParserNew().parse(live)
picked = bot._pick_warp_menu_item(live_state)
ttext = picked.get('text') if picked else None
print(f"12) live pick        -> {ttext} @ ({picked.get('x') if picked else '-'},"
      f"{picked.get('y') if picked else '-'})")
assert picked is not None and picked.get('text') == 'Warp to Within', ttext
assert (picked.get('x'), picked.get('y')) == (664, 119), picked
cx = picked.get('x') + picked.get('w') // 2   # центр строки 'Warp to Within'
cy = picked.get('y') + picked.get('h') // 2
assert 664 <= cx <= 827 and 119 <= cy <= 141, (cx, cy)
print(f"12b) center click    -> 'Warp to Within' в центре строки ({cx},{cy})")

dist_items = [it for it in live_state.menu_items
              if 'within 70 km' in str(it.get('text') or '').lower()]
assert dist_items, "дамл должен содержать 'Within 70 km'"
di = dist_items[0]
dx = di.get('x') + di.get('w') // 2           # центр строки 'Within 70 km'
dy = di.get('y') + di.get('h') // 2
assert 837 <= dx <= 941 and 229 <= dy <= 251, (dx, dy)
print(f"12c) 'Within 70 km'  -> центр клика ({dx},{dy}) попадает в строку")

# ===== 13. МОДАЛЬНОЕ ОКНО INFORMATION =====
print("\n--- 13. Модальное окно Information ---")

# Создаём состояние с открытым модальным окном Information
from ParsedUIState import ParsedUIState

modal_state = ParsedUIState()
modal_state.modal_info = {
    'open': True,
    'name': 'Information',
    'x': 569, 'y': 299, 'w': 400, 'h': 400,
    'close': {'x': 0, 'y': 324, 'w': 380, 'h': 32}  # нижняя кнопка Close
}

# 13a: первая реакция на модалку — Enter (клик Close только запасной вариант)
reset_mock()
bot.think(modal_state, use_drones=True)
keys = pressed_keys()
assert keys == ['enter'], f"первая реакция на модалку — Enter, а не {keys}"
assert not pag.click.call_args_list, "при первых попытках кликов быть не должно"
print("13a) Information открыта -> нажат Enter (1-я попытка)")

# 13a2: модалка висит после 2 Enter -> клик по центру кнопки Close
#       (0 + 380//2, 324 + 32//2) = (190, 340)
reset_mock()
bot.think(modal_state, use_drones=True)   # попытка 2 -> снова Enter
assert pressed_keys() == ['enter'], "попытка 2 — тоже Enter"
assert not pag.click.call_args_list, "на 2-й попытке клика ещё быть не должно"
reset_mock()
bot.think(modal_state, use_drones=True)   # попытка 3 -> клик по Close
click_calls = pag.click.call_args_list
assert click_calls, "на 3-й попытке бот должен кликнуть по Close"
click = click_calls[0]
click_x = click.kwargs.get('x') or (click.args[0] if click.args else None)
click_y = click.kwargs.get('y') or (click.args[1] if len(click.args) > 1 else None)
assert click_x == 190 and click_y == 340, f"клик по Close должен быть (190,340), а не ({click_x},{click_y})"
assert 'enter' not in pressed_keys(), "на 3-й попытке Enter уже не нажимается (только клик)"
print(f"13a2) Enter не помог -> клик по Close ({click_x},{click_y}) — центр кнопки")

# 13b: без кнопки Close — Enter на каждой попытке (кликать некуда)
modal_state2 = ParsedUIState()
modal_state2.modal_info = {
    'open': True,
    'name': 'Information',
    'x': 569, 'y': 299, 'w': 400, 'h': 400,
    'close': None  # кнопка не найдена
}

reset_mock()
for _ in range(4):
    bot.think(modal_state2, use_drones=True)
keys = pressed_keys()
assert keys == ['enter'] * 4, f"без Close должны быть только Enter, а не {keys}"
assert not pag.click.call_args_list, "без кнопки Close кликов быть не должно"
print("13b) Information без кнопки Close -> Enter на каждой попытке")

# Модалка закрыта — бот НЕ должен кликать и не должен нажимать Enter,
# плюс счётчик попыток сбрасывается
modal_state3 = ParsedUIState()
modal_state3.modal_info = {'open': False}

reset_mock()
bot.think(modal_state3, use_drones=True)
assert not pag.click.call_args_list, "при закрытой модалке кликов не должно быть"
assert 'enter' not in pressed_keys(), "при закрытой модалке Enter не нажимается"
assert bot._modal_attempts == 0, "счётчик попыток должен сброситься после закрытия модалки"
print("13c) Модалка закрыта -> нет кликов и Enter, счётчик попыток сброшен")

# 13d: новая модалка после закрытия -> опять сначала Enter (счётчик реально обнулился)
modal_state3.modal_info = {
    'open': True,
    'name': 'Information',
    'x': 569, 'y': 299, 'w': 400, 'h': 400,
    'close': {'x': 0, 'y': 324, 'w': 380, 'h': 32}
}
reset_mock()
bot.think(modal_state3, use_drones=True)
assert pressed_keys() == ['enter'] and not pag.click.call_args_list, \
    "после сброса новая модалка снова должна закрываться Enter'ом"
print("13d) Новая модалка после закрытия -> снова сначала Enter")

# ===== 14. ЖИВОЙ ДАМП raw_dump_20260902_191755.json (модалка после варпа) =====
print("\n--- 14. Живой дамп: Information 'Sansha Hidden Rally Point' ---")
from full_parser import parse_modal_info

with open("raw_dump_20260902_191755.json", "r", encoding="utf-8") as f:
    live_dump = json.load(f)

modal = parse_modal_info(live_dump)
assert modal['open'] is True, f"модалка должна быть найдена: {modal}"
assert modal['name'] == 'Information', f"заголовок окна: {modal['name']!r}"
wx, wy, ww, wh = modal['x'], modal['y'], modal['w'], modal['h']
cb = modal['close']
assert cb, f"в живом дампе должна найтись кнопка Close: {modal}"
# Кнопка обязана лежать внутри окна (иначе координаты задвоены — старый баг)
assert wx <= cb['x'] and cb['x'] + cb['w'] <= wx + ww, f"Close вне окна по X: {modal}"
assert wy <= cb['y'] and cb['y'] + cb['h'] <= wy + wh, f"Close вне окна по Y: {modal}"
print(f"14a) parse_modal_info: window=({wx},{wy}) {ww}x{wh}, close={cb} — внутри окна")

# 14b: бот реагирует на живую модалку Enter'ом (и только им, раз это первая попытка)
live_state = ParsedUIState()
live_state.modal_info = modal
bot._modal_attempts = 0
reset_mock()
bot.think(live_state, use_drones=True)
assert pressed_keys() == ['enter'], f"живая модалка -> Enter, а не {pressed_keys()}"
assert not pag.click.call_args_list, "на первой попытке клика быть не должно"
print("14b) живая модалка Information -> Enter")

print("\nALL BOT INTEGRATION ASSERTIONS PASSED")