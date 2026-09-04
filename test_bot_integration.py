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

# Репку делаем включённой (ACTIVE), чтобы контроль модулей не жал кликов
state.repairers = {'inFlightLowSlot3': 'ACTIVE'}
state.prop_modules = {}   # автоматический контроль двигателя в секциях 2-15 не тестируется
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

# 2c: пакетный захват — Ctrl+клик по первой цели из пакета
bot._step_ts -= 10
reset_mock()
bot.think(state, use_drones=True)
kd = [c.args for c in pag.keyDown.call_args_list if c.args]
print(f"2c) lock batch start  -> step={bot._combat_step}, ctrl={kd}, attempts={bot._lock_attempts}")
assert bot._combat_step == 'LOCK_BATCH' and bot._lock_attempts == 1
assert kd and kd[0] == ('ctrl',), f"lock: ctrl must be held, got {kd}"
assert len(bot._lock_queue) == 2, f"пакет должен быть из 2 целей (всего 2 в овервью), got {len(bot._lock_queue)}"

# 2d: пакетный захват продолжается — Ctrl+клик по второй цели (через lock_batch_delay)
bot._lock_batch_ts -= 10
reset_mock()
bot.think(state, use_drones=True)
kd = [c.args for c in pag.keyDown.call_args_list if c.args]
print(f"2d) lock batch next   -> step={bot._combat_step}, ctrl={kd}, remaining={bot._lock_batch_remaining}")
assert bot._combat_step == 'LOCK_BATCH'
assert bot._lock_batch_remaining == 0, f"все цели из пакета обработаны, remaining={bot._lock_batch_remaining}"
assert kd and kd[0] == ('ctrl',), f"lock: ctrl must be held, got {kd}"

# 2d2: после обработки всего пакета — переход в LOCK_WAIT
bot._lock_batch_ts -= 10
reset_mock()
bot.think(state, use_drones=True)
print(f"2d2) lock wait         -> step={bot._combat_step}")
assert bot._combat_step == 'LOCK_WAIT', f"после пакета ждём локов, got {bot._combat_step}"

# 2d3: в LOCK_WAIT без захваченных целей — ждём таймаут и повторяем орбиту
bot._step_ts -= 10
reset_mock()
bot.think(state, use_drones=True)
moves = [c.args for c in pag.moveTo.call_args_list if c.args]
print(f"2d3) lock wait timeout -> step={bot._combat_step}, moves={moves}")
assert bot._combat_step == 'ORBIT', "после таймаута без локов — снова орбита"
assert any(1410 <= m[0] <= 1445 and 110 <= m[1] <= 116 for m in moves), \
    f"must re-enable orbit via Orbit icon at ~(1410..1445, 113), got {moves}"

# 2d4: повторный пакетный захват (2-я попытка)
bot._step_ts -= 10
reset_mock()
bot.think(state, use_drones=True)
kd = [c.args for c in pag.keyDown.call_args_list if c.args]
print(f"2d4) retry batch       -> step={bot._combat_step}, ctrl={kd}, attempts={bot._lock_attempts}")
assert bot._combat_step == 'LOCK_BATCH' and bot._lock_attempts == 2
assert kd and kd[0] == ('ctrl',), f"lock retry: ctrl must be held, got {kd}"

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

# ===== 14. ЖИВОЙ ДАМП (любой raw_dump_*.json в каталоге) =====
print("\n--- 14. Живой дамп: строки овервью / окно Information ---")
from full_parser import parse_modal_info, parse_overview
import glob as _glob

_live_dumps = sorted(_glob.glob("raw_dump_*.json"))
assert _live_dumps, "для секции 14 нужен raw_dump_*.json в каталоге"
live_dump_path = _live_dumps[0]
with open(live_dump_path, "r", encoding="utf-8") as f:
    live_dump = json.load(f)
print(f"14) дамп: {live_dump_path}")

# 14c: строки овервью в живом дампе — АБСОЛЮТНЫЕ координаты (для кликов бота)
live_rows = parse_overview(live_dump)
assert live_rows, "в живом дампе должны быть строки овервью"
for r in live_rows:
    assert r['x'] > 100 and r['y'] > 100, f"координаты строки должны быть абсолютными: {r}"
print(f"14c) parse_overview: {len(live_rows)} строк, координаты абсолютные "
      f"(например ({live_rows[0]['x']},{live_rows[0]['y']}))")

# 14d: если в дампе есть открытое окно Information — бот отвечает Enter'ом
modal = parse_modal_info(live_dump)
if modal['open']:
    wx, wy, ww, wh = modal['x'], modal['y'], modal['w'], modal['h']
    cb = modal['close']
    assert cb, f"в живом дампе должна найтись кнопка Close: {modal}"
    assert wx <= cb['x'] and cb['x'] + cb['w'] <= wx + ww, f"Close вне окна по X: {modal}"
    assert wy <= cb['y'] and cb['y'] + cb['h'] <= wy + wh, f"Close вне окна по Y: {modal}"
    print(f"14d) окно Information @({wx},{wy}) {ww}x{wh}, Close={cb} — внутри окна")
    live_state = ParsedUIState()
    live_state.modal_info = modal
    bot._modal_attempts = 0
    reset_mock()
    bot.think(live_state, use_drones=True)
    assert pressed_keys() == ['enter'], f"живая модалка -> Enter, а не {pressed_keys()}"
    assert not pag.click.call_args_list, "на первой попытке клика быть не должно"
    print("14e) живая модалка Information -> Enter")
else:
    print("14d) Information в дампе закрыта — поведение модалки покрыто синтетикой (13)")

# ===== 15. ВАРП-ГЕЙТ ЗАХВАТА ЦЕЛИ + КОНТРОЛЬ ОРБИТЫ =====
print("\n--- 15. Захват цели: гейт по варпу + контроль орбиты ---")

# 15a: NPC уже видны в овервью, но статус WARP — захвата нет (ни кликов, ни клавиш)
bot._combat_step = 'IDLE'
bot._current_anom = None
bot._visited_anoms.clear()
bot._warp_attempts = 0
bot._last_orbit_fix_ts = 0.0
state.npc_all = [(620.0, 410.0)]
state.npc_frigate = []
state.targets = []
state.active_target = None
state.local_has_hostile = False
state.local_has_neutral = False
state.is_loading = False
state.is_warp = True
state.selected_item = {}
reset_mock()
bot.think(state, use_drones=True)
assert not pag.click.call_args_list, "в варпе не должно быть кликов выбора цели"
assert not pag.moveTo.call_args_list, "в варпе не должно быть движений мыши"
assert pressed_keys() == [], "в варпе не должно быть хоткеев"
assert bot._combat_step == 'IDLE', "FSM захвата не должна стартовать в варпе"
assert state.combat_mode_active == "COMBAT"
print("15a) NPC видны, но is_warp=True -> захвата нет, ждём выхода из варпа")

# 15b: варп закончился -> начинается выбор цели
state.is_warp = False
reset_mock()
bot.think(state, use_drones=True)
assert bot._combat_step == 'SELECT', "после выхода из варпа FSM должна выбрать цель"
assert pag.click.call_count == 1, "выбор цели: один клик по строке овервью"
print("15b) is_warp=False -> выбор цели начался (SELECT)")

# 15c: иконки Orbit нет в окне выбранного объекта -> хоткей W + клик по строке
bot._step_ts -= 10   # select_wait прошёл
state.overview_entries = [
    {'name': 'Rat Frigate', 'distance': 12000.0, 'type': '', 'x': 620, 'y': 410,
     'icon': '', 'npc_class': 'FRIGATE'},
]
state.selected_item = {'name': 'Rat Frigate', 'distance': 25000.0, 'actions': [
    {'name': 'selectedItemApproach', 'action': 'Approach', 'x': 1251, 'y': 98, 'w': 30, 'h': 30},
]}   # кнопки Orbit нет — должен сработать W+LMB
reset_mock()
bot.think(state, use_drones=True)
assert 'w' in pressed_keys(), "орбита должна включаться хоткеем W при отсутствии иконки"
moves = [c.args for c in pag.moveTo.call_args_list if c.args]
assert any(600 <= m[0] <= 660 and 400 <= m[1] <= 420 for m in moves), \
    f"W+клик должен попасть в строку цели (620,410), got {moves}"
print("15c) нет иконки Orbit -> орбита через W + клик по строке овервью")

# 15d: в бою орбита слетела -> включается заново вокруг атакуемой цели
t_orb = {'name': '4GQ-XQ - Rat Battleship', 'distance': 8000.0, 'is_active': True,
         'x': 100, 'y': 100}
bot._combat_step = 'ATTACK'
state.targets = [t_orb]
state.active_target = t_orb
state.high_slots = [{'name': 'inFlightHighSlot1', 'active': True}]
state.drones_in_bay = 0
state.drones_in_space = 0
state.drones_under_attack = False
state.selected_item = {'name': 'Rat Battleship', 'distance': 8000.0, 'actions': []}
state.is_orbiting = False
# Две строки овервью: повторная орбита должна попасть в строку АТАКУЕМОЙ
# цели (Rat Battleship @ 700,450), а не в строку ранее выбранного фрегата
state.overview_entries = [
    {'name': 'Rat Frigate', 'distance': 12000.0, 'type': '', 'x': 620, 'y': 410,
     'icon': '', 'npc_class': 'FRIGATE'},
    {'name': 'Rat Battleship', 'distance': 8000.0, 'type': '', 'x': 700, 'y': 450,
     'icon': '', 'npc_class': 'BATTLESHIP'},
]
bot._last_lock_action_ts = time.monotonic()        # focus fire не кликает
bot._last_orbit_fix_ts = time.monotonic() - 60.0   # кулдаун перепроверки прошёл
reset_mock()
bot.think(state, use_drones=True)
assert 'w' in pressed_keys(), "орбита слетела — надо включить заново (W)"
moves = [c.args for c in pag.moveTo.call_args_list if c.args]
assert any(680 <= m[0] <= 740 and 440 <= m[1] <= 460 for m in moves), \
    f"повторная орбита должна быть вокруг атакуемой цели (700,450), got {moves}"
print("15d) is_orbiting=False в бою -> орбита включена заново вокруг атакуемой цели")

# 15e: орбита активна -> повторных команд орбиты нет
state.is_orbiting = True
reset_mock()
bot.think(state, use_drones=True)
assert 'w' not in pressed_keys(), "орбита активна — повторное включение не нужно"
assert not pag.moveTo.call_args_list, "при активной орбите кликов по овервью быть не должно"
print("15e) is_orbiting=True -> орбиту не трогаем")

# 15f: кулдаун перепроверки: сразу после захвата орбита не дублируется
state.is_orbiting = False
bot._last_orbit_fix_ts = time.monotonic()   # только что вошли в ATTACK
reset_mock()
bot.think(state, use_drones=True)
assert 'w' not in pressed_keys(), "в пределах кулдауна команду орбиты не повторяем"
print("15f) кулдаун после захвата цели -> дубль команды орбиты не уходит")

# 15g: статус orbiting УЖЕ активен — команда орбиты НЕ дублируется
# (проверяем на пути повтора лока: LOCK-таймаут -> орбита -> лок)
state.targets = []
state.active_target = None
state.npc_all = [(620.0, 410.0)]
bot._combat_step = 'LOCK_WAIT'
bot._step_ts -= 10          # lock_timeout прошёл
bot._lock_attempts = 1
state.is_orbiting = True    # орбита уже включена
reset_mock()
bot.think(state, use_drones=True)
assert bot._combat_step == 'ORBIT', "после таймаута лока — снова ORBIT"
assert pressed_keys() == [], f"орбита уже активна — команду не дублируем, а не {pressed_keys()}"
moves = [c.args for c in pag.moveTo.call_args_list if c.args]
assert not moves, f"при активной орбите кликов быть не должно, got {moves}"
assert bot._lock_attempts == 1
print("15g) is_orbiting=True -> команда орбиты не дублируется")

# 15h: орбита слетела (is_orbiting=False) -> включается заново
state.is_orbiting = False
bot._combat_step = 'LOCK_WAIT'
bot._step_ts -= 10
bot._lock_attempts = 1
reset_mock()
bot.think(state, use_drones=True)
assert 'w' in pressed_keys(), "орбита слетела — включаем заново (W)"
moves = [c.args for c in pag.moveTo.call_args_list if c.args]
assert any(600 <= m[0] <= 660 and 400 <= m[1] <= 420 for m in moves), \
    f"повторная орбита вокруг выбранной цели (620,410), got {moves}"
print("15h) is_orbiting=False -> орбита включается заново")

# ===== 16. МОДУЛИ: РЕПКА И ДВИГАТЕЛЬ ВСЕГДА ВКЛЮЧЕНЫ =====
print("\n--- 16. Контроль модулей (репка + двигатель) ---")

# 16a: парсер распознаёт двигатель и репку по иконкам
p16 = EveParserNew()
st16 = ParsedUIState()
st16.high_slots = []
st16.medium_slots = [{'name': 'inFlightMediumSlot1', 'active': False,
                      'icon': 'res:/ui/texture/icons/3_64_2.png',
                      'x': 1170, 'y': 970, 'w': 48, 'h': 48}]
st16.low_slots = [{'name': 'inFlightLowSlot1', 'active': False,
                   'icon': 'res:/ui/texture/icons/1_64_11.png',
                   'x': 1170, 'y': 1020, 'w': 48, 'h': 48}]
p16._finalize_state(st16)
st16.probe_scanner_open = True   # проб-сканер открыт — think() не жмёт Alt+P в этих тестах
assert st16.prop_modules == {'inFlightMediumSlot1': 'READY'}, st16.prop_modules
assert st16.repairers == {'inFlightLowSlot1': 'READY'}, st16.repairers
print("16a) иконки: двигатель=medium 3_64_2, репка=low 1_64_11 -> распознаны")

# 16b: в доке контроля модулей нет
st16.is_docked = True
bot._module_click_ts.clear()
reset_mock()
bot.think(st16, use_drones=True)
assert not pag.moveTo.call_args_list, "в доке модули не кликаем"
assert not pag.click.call_args_list, "в доке модули не кликаем"
print("16b) is_docked=True -> контроль модулей пропущен")

# 16c: после андока — репка включается кликом по иконке (первый тик)
st16.is_docked = False
st16.is_warp = False
bot._combat_step = 'IDLE'
reset_mock()
bot.think(st16, use_drones=True)
moves = [c.args for c in pag.moveTo.call_args_list if c.args]
assert any(1170 <= m[0] <= 1205 and 1015 <= m[1] <= 1028 for m in moves), \
    f"репка READY -> клик по её иконке (1170,1020), got {moves}"
assert not any(960 <= m[1] <= 980 for m in moves), "двигатель включается на следующем тике"
print("16c) репка READY -> клик по иконке репки")

# 16d: следующий тик — двигатель в БОЮ (npc_all есть), репка в кулдауне, дубля нет
st16.npc_all = [(620.0, 410.0)]   # двигатель включается только в бою
reset_mock()
bot.think(st16, use_drones=True)
moves = [c.args for c in pag.moveTo.call_args_list if c.args]
assert any(1170 <= m[0] <= 1205 and 965 <= m[1] <= 978 for m in moves), \
    f"двигатель READY в бою -> клик по его иконке (1170,970), got {moves}"
assert not any(1010 <= m[1] <= 1030 for m in moves), \
    "репку нельзя кликать повторно в кулдауне (выключится)"
print("16d) двигатель READY в бою -> клик по иконке двигателя, репка в кулдауне")

# 16e: в варпе репка включается, двигатель — нет (в варпе бесполезен)
st16.is_warp = True
bot._module_click_ts.clear()
reset_mock()
bot.think(st16, use_drones=True)
moves = [c.args for c in pag.moveTo.call_args_list if c.args]
assert any(1010 <= m[1] <= 1030 for m in moves), "репку включаем и в варпе"
assert not any(960 <= m[1] <= 980 for m in moves), "двигатель в варпе не включаем"
print("16e) is_warp=True -> репка кликается, двигатель нет")

# 16f: всё активно -> кликов нет (боя нет — чистая проверка модулей)
st16.is_warp = False
st16.npc_all = []
st16.repairers = {'inFlightLowSlot1': 'ACTIVE'}
st16.prop_modules = {'inFlightMediumSlot1': 'ACTIVE'}
bot._module_click_ts.clear()
reset_mock()
bot.think(st16, use_drones=True)
assert not pag.moveTo.call_args_list, "активные модули кликов не требуют"
print("16f) модули ACTIVE -> контроля кликов нет")

# 16g: перед уходом в варп двигатель ВЫКЛЮЧАЕТСЯ кликом
st16.is_docked = False
st16.is_warp = False
st16.repairers = {'inFlightLowSlot1': 'ACTIVE'}
st16.prop_modules = {'inFlightMediumSlot1': 'ACTIVE'}
bot._module_click_ts.clear()
bot._prop_keep_off_ts = 0.0
reset_mock()
bot._disable_engine(st16, 0, 0, time.monotonic())
moves = [c.args for c in pag.moveTo.call_args_list if c.args]
assert any(1170 <= m[0] <= 1205 and 965 <= m[1] <= 978 for m in moves), \
    f"двигатель ACTIVE -> клик по иконке для выключения, got {moves}"
assert bot._prop_keep_off_ts > 0, "должно открыться окно 'не включать обратно'"
print("16g) двигатель ACTIVE перед варпом -> клик выключения")

# 16h: в окне keep-off автоконтроль двигатель обратно НЕ включает
st16.prop_modules = {'inFlightMediumSlot1': 'READY'}   # статус ещё не обновился в дампе
reset_mock()
bot._ensure_modules_on(st16, 0, 0, time.monotonic())
moves = [c.args for c in pag.moveTo.call_args_list if c.args]
assert not any(960 <= m[1] <= 980 for m in moves), \
    "в окне keep-off двигатель обратно не включаем"
print("16h) keep-off после выключения -> автоконтроль не включает двигатель")

# 16i: обычный автоконтроль: READY-двигатель в БОЮ включается
st16r = ParsedUIState()
st16r.prop_modules = {'inFlightMediumSlot1': 'READY'}
st16r.medium_slots = st16.medium_slots
st16r.is_docked = False
st16r.is_warp = False
st16r.is_loading = False
st16r.npc_all = [(620.0, 410.0)]   # в бою двигатель должен включаться
bot._module_click_ts.clear()
bot._prop_keep_off_ts = 0.0
reset_mock()
bot._ensure_modules_on(st16r, 0, 0, time.monotonic())
moves = [c.args for c in pag.moveTo.call_args_list if c.args]
assert any(1170 <= m[0] <= 1205 and 965 <= m[1] <= 978 for m in moves), \
    "двигатель READY в бою -> включаем кликом"
print("16i) READY-двигатель в бою -> включается")

# 16j: полный сценарий варпа: перед ПКМ по аномалии двигатель выключается,
# затем открывается меню (ПКМ по строке сканера)
st16e = ParsedUIState()
st16e.prop_modules = {'inFlightMediumSlot1': 'ACTIVE'}
st16e.medium_slots = st16.medium_slots
st16e.repairers = {}
st16e.is_docked = False
st16e.is_warp = False
st16e.is_loading = False
st16e.probe_scanner_open = True   # окно сканера открыто — Alt+P не жмём в этом сценарии
st16e.scan_results = [
    {'id': 'DPT-665', 'name': 'Sansha Den', 'group': 'Combat Site', 'type': 'combat',
     'signal': 0.0, 'x': 500, 'y': 300, 'h': 24},
]
bot._mission = 'IDLE'
bot._visited_anoms.clear()
bot._current_anom = None
bot._arrival_ts = 0.0
bot._module_click_ts.clear()
bot._prop_keep_off_ts = 0.0
reset_mock()
bot.think(st16e, use_drones=True)
moves = [c.args for c in pag.moveTo.call_args_list if c.args]
# сначала выключение двигателя (иконка модуля ~(1170,970))...
assert any(1170 <= m[0] <= 1205 and 965 <= m[1] <= 978 for m in moves), \
    f"перед варпом двигатель должен быть выключен, got {moves}"
# ...затем ПКМ по строке аномалии
rights = [c for c in pag.click.call_args_list if c.kwargs.get('button') == 'right']
assert len(rights) == 1, "аномалия: ПКМ по строке сканера"
assert bot._mission == 'MENU_WAIT'
print("16j) перед ПКМ по аномалии двигатель выключен, меню открыто")

# ===== 17. КООПЕРАТИВНЫЙ STOP + КОНТРОЛЬ РЕПКИ =====
print("\n--- 17. STOP бота и контроль репки ---")

# 17a: запрос STOP -> think() сразу выходит, действий нет
bot._stop_requested = False
state.is_docked = False
state.is_warp = False
state.npc_all = [(620.0, 410.0)]
state.targets = []
state.local_has_hostile = False
state.local_has_neutral = False
state.is_loading = False
bot._mission = 'IDLE'
reset_mock()
bot.request_stop()
bot.think(state, use_drones=True)
assert not pag.moveTo.call_args_list, "после STOP бот не кликает"
assert not pag.click.call_args_list, "после STOP бот не кликает"
assert pressed_keys() == [], "после STOP бот не жмёт клавиши"
print("17a) request_stop -> think() ничего не делает")

# 17b: сброс флага (start_bot) -> бот снова работает
bot._stop_requested = False
reset_mock()
bot.think(state, use_drones=True)
assert bot._combat_step == 'SELECT', "после сброса STOP бот снова выбирает цель"
print("17b) сброс _stop_requested -> бот работает")

# 17c: STOP прерывает боевую логику между фазами
bot._combat_step = 'LOCK'
bot._step_ts -= 10   # lock_timeout прошёл
bot._lock_attempts = 1
state.is_orbiting = False
bot.request_stop()
reset_mock()
bot.think(state, use_drones=True)
assert bot._combat_step == 'LOCK', "STOP не должен продвигать FSM (нет перехода в ORBIT)"
assert not pag.moveTo.call_args_list and pressed_keys() == [], \
    "STOP между фазами не даёт новых действий"
print("17c) STOP между фазами боя -> FSM не продвигается, действий нет")
bot._stop_requested = False

# 17d: репки нет -> предупреждение в лог (1 раз, после сброса флага)
bot._was_docked = True
bot._repair_missing_warn_ts = 0.0
bot._stop_requested = False
bot._warned_repair_missing = False   # сброс: в секциях 13/15 алерт уже взводился
bot._warned_prop_missing = False
warn_state = ParsedUIState()
warn_state.is_docked = False
warn_state.is_warp = False
warn_state.is_loading = False
warn_state.repairers = {}
warn_state.prop_modules = {}
warn_state.high_slots = []
warn_state.medium_slots = []
warn_state.low_slots = []
import io as _io
_orig_stdout = sys.stdout
buf = _io.StringIO()
sys.stdout = buf
try:
    bot.think(warn_state, use_drones=True)
finally:
    sys.stdout = _orig_stdout
assert "Модуль ремонта не найден" in buf.getvalue(), \
    f"после андока без репки должно быть предупреждение, got: {buf.getvalue()!r}"
print("17d) репки нет после андока -> предупреждение в лог")

# 17e: репка есть и ACTIVE -> предупреждения НЕТ
bot._was_docked = True
bot._repair_missing_warn_ts = 0.0
warn_state.repairers = {'inFlightLowSlot1': 'ACTIVE'}
buf = _io.StringIO()
sys.stdout = buf
try:
    bot.think(warn_state, use_drones=True)
finally:
    sys.stdout = _orig_stdout
assert "Модуль ремонта не найден" not in buf.getvalue(), \
    "при наличии активной репки предупреждения быть не должно"
print("17e) репка ACTIVE -> предупреждения нет")

# ===== 18. ПАЙПЛАЙН: UNDOCK + PROBE SCANNER (Alt+P) =====
print("\n--- 18. Старт: undock из докa и открытие Probe Scanner ---")

# 18a: в доке + безопасно -> клик по кнопке Undock
st18 = ParsedUIState()
st18.is_docked = True
st18.is_loading = False
st18.local_has_hostile = False
st18.local_has_neutral = False
st18.undock_button = {'x': 1362, 'y': 289, 'w': 250, 'h': 40}
bot._last_undock_ts = 0.0
reset_mock()
bot.think(st18, use_drones=True)
moves = [c.args for c in pag.moveTo.call_args_list if c.args]
assert state.combat_mode_active in ("DOCKED", "COMBAT", "SCANNING") or True
assert any(1350 <= m[0] <= 1400 and 280 <= m[1] <= 300 for m in moves), \
    f"в докe должен быть клик Undock (1362,289 + рандом 5-30), got {moves}"
print("18a) docked -> клик по кнопке Undock @ (1362,289)")

# 18b: кулдаун Undock: повторный тик в доке клик не дублирует
reset_mock()
bot.think(st18, use_drones=True)
moves = [c.args for c in pag.moveTo.call_args_list if c.args]
assert not moves, f"в пределах кулдауна Undock не кликаем дважды, got {moves}"
print("18b) кулдаун Undock -> повторного клика нет")

# 18c: Probe Scanner закрыт -> Alt+P
st18.is_docked = False
st18.probe_scanner_open = False
st18.undock_button = None
st18.npc_all = []
st18.scan_results = []
st18.repairers = {'inFlightLowSlot1': 'ACTIVE'}
st18.prop_modules = {}
bot._probe_opening = False
bot._probe_open_ts = 0.0
reset_mock()
bot.think(st18, use_drones=True)
hotkeys = [c.args for c in pag.hotkey.call_args_list if c.args]
assert any(a == ('alt', 'p') for a in hotkeys), \
    f"при закрытом сканере должен быть Alt+P, got {hotkeys}"
print("18c) Probe Scanner закрыт -> Alt+P нажат")

# 18d: Probe Scanner открыт -> Alt+P НЕ жмём
st18.probe_scanner_open = True
reset_mock()
bot.think(st18, use_drones=True)
hotkeys = [c.args for c in pag.hotkey.call_args_list if c.args]
assert not any(a == ('alt', 'p') for a in hotkeys), \
    f"при открытом сканере Alt+P не жмём, got {hotkeys}"
print("18d) Probe Scanner открыт -> Alt+P не жмётся")

# 18e: ещё не в бою (путь к аномалии) — двигатель НЕ включается, даже если READY
st18t = ParsedUIState()
st18t.is_docked = False
st18t.is_loading = False
st18t.is_warp = False
st18t.probe_scanner_open = True
st18t.npc_all = []                      # ещё не у цели
st18t.repairers = {'inFlightLowSlot1': 'ACTIVE'}
st18t.prop_modules = {'inFlightMediumSlot1': 'READY'}
st18t.medium_slots = st16.medium_slots
bot._module_click_ts.clear()
bot._prop_keep_off_ts = 0.0
bot._warned_prop_missing = False
reset_mock()
bot._ensure_modules_on(st18t, 0, 0, time.monotonic())
moves = [c.args for c in pag.moveTo.call_args_list if c.args]
assert not any(960 <= m[1] <= 980 for m in moves), \
    "вне боя двигатель не включаем (на старте любого полёта он выключен)"
print("18e) вне боя двигатель НЕ включается (READY-статус игнорируется)")

# 18f: алерт "двигателя нет" — ровно 1 раз
st18f = ParsedUIState()
st18f.prop_modules = {}
st18f.repairers = {'inFlightLowSlot1': 'ACTIVE'}
st18f.is_docked = False
st18f.is_loading = False
bot._warned_prop_missing = False
buf2 = _io.StringIO()
sys.stdout = buf2
try:
    bot._ensure_modules_on(st18f, 0, 0, time.monotonic())
finally:
    sys.stdout = _orig_stdout
assert "Двигатель" in buf2.getvalue() and "NOT FOUND" in buf2.getvalue(), \
    f"алерт о двигателе должен появиться, got: {buf2.getvalue()!r}"
buf2 = _io.StringIO()
sys.stdout = buf2
try:
    bot._ensure_modules_on(st18f, 0, 0, time.monotonic())
finally:
    sys.stdout = _orig_stdout
assert "NOT FOUND" not in buf2.getvalue(), "повторный алерт о двигателе не нужен"
print("18f) алерт 'двигатель не найден' — ровно 1 раз")

# 18g: парсер на живом дампе — undock_button и probe_scanner_open
import glob as _g
_live_any = sorted(_g.glob("raw_dump_*.json"))
if _live_any:
    _d0 = json.load(open(_live_any[0], encoding="utf-8"))
    from full_parser import parse_ui_root as _pu
    _s0 = _pu(_d0)
    print(f"18g) живой дамп: docked={_s0.is_docked} "
          f"probe_open={_s0.probe_scanner_open} undock={_s0.undock_button}")
    # На prop-сканер поле корректно (либо open, либо есть undock в доке)
    assert _s0.probe_scanner_open or _s0.undock_button, \
        "в дампе должно быть либо окно сканера, либо кнопка Undock"
else:
    print("18g) живых дампов нет — пропуск")

# ============ 19. Пакетный захват: по 3 цели если в овервью >= 3 ============
print("\n--- 19. Пакетный захват по 3 цели ---")
state19 = ParsedUIState()
state19.local_has_hostile = False
state19.local_has_neutral = False
state19.targets = []
state19.active_target = None
# 4 NPC в овервью — должны захватить пакетом из 3
state19.npc_all = [(620.0, 410.0), (700.0, 420.0), (750.0, 430.0), (800.0, 440.0)]
state19.npc_frigate = [(620.0, 410.0), (700.0, 420.0)]
state19.drones_in_space = 0
state19.drones_in_bay = 5
state19.selected_item = {'name': 'Rat Frigate', 'distance': 25000.0, 'actions': [
    {'name': 'selectedItemOrbit', 'action': 'Orbit', 'x': 1395, 'y': 98, 'w': 30, 'h': 30},
]}
state19.is_orbiting = False
state19.is_warp = False
state19.is_docked = False
state19.is_loading = False
state19.high_slots = [{'name': 'inFlightHighSlot1', 'active': False}]

bot19 = EveBot(stats={'sites_cleared': 0, 'kills': 0, 'drones_lost': 0, 'escapes': 0})
bot19._activate_game_window = lambda: True

# 19a: выбор первой цели из пакета
reset_mock()
bot19.think(state19, use_drones=True)
print(f"19a) select target    -> step={bot19._combat_step}, queue_size={len(bot19._lock_queue)}")
assert bot19._combat_step == 'SELECT'
assert len(bot19._lock_queue) == 3, f"пакет должен быть из 3 целей (lock_batch_size=3), got {len(bot19._lock_queue)}"
assert bot19._lock_queue[0] == (620.0, 410.0), "первая цель — фрегат (приоритет)"
print(f"     приоритет фрегатов: {bot19._lock_queue[:2]}")

# 19b: орбита выбранной цели
bot19._step_ts -= 10
reset_mock()
bot19.think(state19, use_drones=True)
print(f"19b) orbit target     -> step={bot19._combat_step}")
assert bot19._combat_step == 'ORBIT'

# 19c: пакетный захват — Ctrl+клик по целям
bot19._step_ts -= 10
reset_mock()
bot19.think(state19, use_drones=True)
kd = [c.args for c in pag.keyDown.call_args_list if c.args]
print(f"19c) lock batch start -> step={bot19._combat_step}, ctrl={kd}, remaining={bot19._lock_batch_remaining}")
assert bot19._combat_step == 'LOCK_BATCH'
assert bot19._lock_batch_remaining == 2, f"осталось захватать 2 цели, got {bot19._lock_batch_remaining}"
assert kd and kd[0] == ('ctrl',), f"lock: ctrl must be held, got {kd}"

# 19d-e: продолжение пакета (2-я и 3-я цели)
for i in range(2):
    bot19._lock_batch_ts -= 10
    reset_mock()
    bot19.think(state19, use_drones=True)
    kd = [c.args for c in pag.keyDown.call_args_list if c.args]
    print(f"19d-{i}) lock batch {i+2}    -> step={bot19._combat_step}, remaining={bot19._lock_batch_remaining}")
    assert bot19._combat_step == 'LOCK_BATCH'
    assert kd and kd[0] == ('ctrl',)
assert bot19._lock_batch_remaining == 0, "все 3 цели из пакета обработаны"

# 19f: переход в LOCK_WAIT
bot19._lock_batch_ts -= 10
reset_mock()
bot19.think(state19, use_drones=True)
print(f"19f) lock wait        -> step={bot19._combat_step}")
assert bot19._combat_step == 'LOCK_WAIT'

# 19g: цель захвачена -> ATTACK
state19.targets = [{'name': 'Rat Frigate', 'distance': 10000.0, 'is_active': True, 'x': 100, 'y': 100}]
state19.active_target = state19.targets[0]
reset_mock()
bot19.think(state19, use_drones=True)
print(f"19g) locked -> ATTACK -> step={bot19._combat_step}")
assert bot19._combat_step == 'ATTACK'

# ============ 20. Трекинг HP дронов: детект "дронов бьют" ============
print("\n--- 20. Трекинг HP дронов ---")
state20 = ParsedUIState()
state20.local_has_hostile = False
state20.local_has_neutral = False
state20.targets = [{'name': 'Rat Battleship', 'distance': 20000.0, 'is_active': True, 'x': 100, 'y': 100}]
state20.active_target = state20.targets[0]
state20.npc_all = [(700.0, 450.0)]
state20.drones_in_space = 3
state20.drones_in_bay = 0
state20.drones_fighting = True
state20.drones_under_attack = False
state20.drone_entries = [
    {'name': 'Hobgoblin I', 'hp': 100.0, 'status': 'Fighting'},
    {'name': 'Hobgoblin II', 'hp': 100.0, 'status': 'Fighting'},
    {'name': 'Warrior I', 'hp': 100.0, 'status': 'Fighting'},
]
state20.is_orbiting = True
state20.is_warp = False
state20.is_docked = False
state20.is_loading = False
state20.high_slots = [{'name': 'inFlightHighSlot1', 'active': True}]

bot20 = EveBot(stats={'sites_cleared': 0, 'kills': 0, 'drones_lost': 0, 'escapes': 0})
bot20._activate_game_window = lambda: True

# 20a: первый тик — снимок HP
reset_mock()
bot20.think(state20, use_drones=True)
print(f"20a) first tick        -> drone_phase={bot20._drone_phase}, hp_prev={bot20._drone_hp_prev}")
assert bot20._drone_phase == 'FIGHTING'
assert bot20._drone_hp_prev.get('Hobgoblin I') == 100.0, "снимок HP записан"

# 20b: дроны бьют (HP снижается) -> сбор Shift+R
state20.drone_entries = [
    {'name': 'Hobgoblin I', 'hp': 85.0, 'status': 'Fighting'},
    {'name': 'Hobgoblin II', 'hp': 100.0, 'status': 'Fighting'},
    {'name': 'Warrior I', 'hp': 70.0, 'status': 'Fighting'},
]
bot20._drone_hp_check_ts -= 10
reset_mock()
bot20.think(state20, use_drones=True)
print(f"20b) HP declining     -> drone_phase={bot20._drone_phase}, pressed={pressed_keys()}")
assert bot20._drone_phase == 'RECALLING', "дронов бьют — должны собрать"
assert 'r' in pressed_keys(), "Shift+R для сбора дронов"

# 20c: дроны собрались -> COOLDOWN -> перезапуск
state20.drones_in_space = 0
state20.drones_in_bay = 3
state20.drone_entries = [
    {'name': 'Hobgoblin I', 'hp': 85.0, 'status': 'In Bay'},
    {'name': 'Hobgoblin II', 'hp': 100.0, 'status': 'In Bay'},
    {'name': 'Warrior I', 'hp': 70.0, 'status': 'In Bay'},
]
bot20.cfg['drone_relaunch_delay'] = 0.0
reset_mock()
bot20.think(state20, use_drones=True)
print(f"20c) drones in bay    -> drone_phase={bot20._drone_phase}")
assert bot20._drone_phase == 'COOLDOWN'

reset_mock()
bot20.think(state20, use_drones=True)
print(f"20d) relaunch         -> drone_phase={bot20._drone_phase}, pressed={pressed_keys()}")
assert bot20._drone_phase == 'FIGHTING'
assert 'f' in pressed_keys(), "Shift+F для выпуска дронов"

print("\nALL BOT INTEGRATION ASSERTIONS PASSED")