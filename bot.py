import pyautogui
import pygetwindow as gw
import time
import random
import re


class EveBot:
    def __init__(self, stats=None):
        self.current_state = None
        self.use_drones = True
        # Небольшая пауза между действиями для стабильности
        pyautogui.PAUSE = 0.8 
        self.window_name = "EVE - Nikky Naari" # Имя окна (может быть "EVE - Имя Персонажа")
        self.no_targets_timer = None # Таймер для сбора дронов
        # Поправка на заголовок окна (если мажет по вертикали)
        self.CORRECTION_Y = 31

        # --- Конфиг боевого поведения (позже вынесем в config.json) ---
        self.cfg = {
            # --- бой: FSM цели ---
            'focus_switch_cooldown': 2.0,  # пауза между переключениями фокуса, сек
            'select_wait': 1.2,            # пауза после клика выбора цели, сек
            'orbit_wait': 2.5,             # пауза после команды орбиты до попытки лока, сек
            'lock_timeout': 8.0,           # ждать появления цели в панели лока, сек
            'lock_max_attempts': 3,        # неудачных попыток лока -> следующая цель
            # --- бой: атака ---
            'auto_attack': True,           # поддерживать цикл оружия (пушки/ракеты)
            'attack_cooldown': 4.0,        # мин. пауза между нажатиями хоткеев оружия, сек
            'key_repress_cooldown': 8.0,   # защита от двойного нажатия (выключит модуль)
            'slot_hotkey_offset': 0,       # inFlightHighSlot1 -> F1 (смещение нумерации)
            # --- дроны ---
            'drone_hp_recall': 0.5,        # доля HP дрона для отзыва (0..1; HP может не парситься)
            'drone_relaunch_delay': 3.0,   # пауза перед перезапуском собранных дронов, сек
            'min_drones': 1,               # тревога, если живых дронов меньше
            # --- миссия: полёт по аномалиям из probe scanner ---
            'anomaly_group_filter': 'combat',   # фильтр по group+name (lower)
            'anomaly_revisit_cooldown': 600,    # сек до повторного визита аномалии
            'menu_open_timeout': 5.0,           # ждать открытия контекстного меню, сек
            'warp_confirm_timeout': 15.0,       # ждать подтверждения варпа, сек
            'warp_max_attempts': 3,             # попыток варпа на одну аномалию
            'warp_distance_km': 70,             # выбор 'within X km' в подменю (потом — config.json)
            'menu_settle': 0.8,                 # пауза после открытия меню до клика по пункту, сек
            'submenu_settle': 1.0,              # пауза после появления подменю до клика по дистанции, сек
            'arrival_settle_time': 15.0,        # пауза после варпа (овервью прогружается), сек
            'modal_enter_attempts': 2,          # сколько раз жать Enter у модалки Information, прежде чем кликать Close
            'orbit_recheck_cooldown': 6.0,      # как часто в бою перепроверять орбиту (сек), чтобы не задваивать команду
            'prop_keep_off_time': 25.0,         # окно "двигатель выключен перед варпом" (сек): меню варпа + старт варпа
            'repair_missing_warn_interval': 60.0,  # как часто предупреждать, если модуля ремонта нет (сек)
        }

        # --- Микро-FSM дронов: FIGHTING | RECALLING | COOLDOWN ---
        self._drone_phase = 'FIGHTING'
        self._drone_cooldown_until = 0.0

        # --- FSM цели в бою: IDLE|SELECT|ORBIT|LOCK|ATTACK ---
        # Цикл: выбор цели из овервью -> орбита -> захват в лок -> атака.
        # Approach не используется: сближение только орбитой вокруг цели.
        self._combat_step = 'IDLE'
        self._step_ts = 0.0
        self._lock_attempts = 0
        self._current_target_coords = None
        self._skip_coords = {}           # координаты целей, не поддающихся локу -> ts
        self._last_attack_press = 0.0
        self._recent_keys = {}           # защита от двойного нажатия (выключит модуль)
        self._last_orbit_fix_ts = 0.0    # последнее повторное включение орбиты (контроль в бою)
        self._module_click_ts = {}       # слот -> ts последнего клика по модулю HUD (анти-даблклик)
        self._prop_keep_off_ts = 0.0     # ts выключения двигателя перед варпом (окно "не включать обратно")

        # --- Модальное окно Information (всплывает после варпа): закрытие Enter -> клик Close ---
        self._modal_attempts = 0

        # --- Миссия: полёт по аномалиям из probe scanner ---
        # IDLE -> MENU_WAIT (ПКМ по строке) -> SUBMENU_WAIT (дистанция) -> WARP_CLICKED
        self._mission = 'IDLE'
        self._mission_ts = 0.0
        self._warp_attempts = 0
        self._current_anom = None
        self._visited_anoms = {}         # имя аномалии -> ts последнего визита
        self._arrival_ts = 0.0           # ts последнего прибытия варпом (settle-пауза)
        self._submenu_seen_ts = None     # ts, когда пункт дистанции впервые появился в подменю

        # --- Расширенное логирование (чекбокс VERBOSE LOG в GUI) ---
        self.verbose = False
        self._vlog_keys = {}             # троттлинг повторяющихся verbose-сообщений
        self._last_vstate_ts = 0.0       # троттлинг сводки состояния

        # --- Служебное: анти-спам кликов и статистика сессии ---
        self._last_lock_action_ts = 0.0
        self._prev_locked_count = 0
        self._had_npcs = False
        self._drone_baseline = None      # сколько дронов было (для детекта потерь)
        self._low_drones_alerted = False

        # --- Кооперативная остановка (кнопка STOP / Ctrl+Shift+S) ---
        self._stop_requested = False

        # --- Контроль модуля ремонта (предупреждение, если репки нет) ---
        self._was_docked = False         # был ли корабль в доке на прошлом тике
        self._repair_missing_warn_ts = 0.0

        # Статистика сессии: bot обновляет, GUI читает
        self.stats = stats if stats is not None else {
            'sites_cleared': 0, 'kills': 0, 'drones_lost': 0, 'escapes': 0,
        }
    
    def _get_offset(self):  
        """Получаем экранные координаты окна игры"""
        try:
            windows = gw.getWindowsWithTitle(self.window_name)
            if windows:
                win = windows[0]
                # Прибавляем коррекцию на заголовок окна к Y
                return win.left, win.top + self.CORRECTION_Y
            return 0, 0
        except:
            return 0, 0

    def _activate_game_window(self):
        """Находит окно игры и выводит его на передний план"""
        try:
            # Ищем окна, в названии которых есть "EVE"
            windows = gw.getWindowsWithTitle(self.window_name)
            if windows:
                win = windows[0]
                if not win.isActive:
                    print(f"[Bot] Активирую окно: {win.title}")
                    win.activate()
                    time.sleep(0.5) # Ждем, пока окно "прогрузится" в фокусе
                return True
            else:
                print("[Bot] Ошибка: Окно EVE не найдено!")
                return False
        except Exception as e:
            print(f"[Bot] Ошибка при активации окна: {e}")
            return False


    def _vlog(self, msg):
        """Расширенный лог (печатается только при включённом VERBOSE LOG)."""
        if self.verbose:
            print(f"[V] {msg}")

    def _vlog_throttled(self, key, msg, interval=2.0):
        """Расширенный лог с троттлингом: ключ не чаще interval секунд."""
        if not self.verbose:
            return
        now = time.time()
        if now - self._vlog_keys.get(key, 0.0) >= interval:
            self._vlog_keys[key] = now
            print(f"[V] {msg}")

    def request_stop(self):
        """Запрос остановки бота (кнопка STOP / Ctrl+Shift+S).

        Флаг проверяется в начале think() и между фазами: текущее действие
        (клик/пауза) завершается, новых действий бот не предпринимает.
        """
        self._stop_requested = True
        print("[Bot] STOP: завершаю текущие действия, новые не начинаю")

    def think(self, state, use_drones: bool):
        # Запрос STOP (кнопка / Ctrl+Shift+S) — немедленный выход из тика
        if self._stop_requested:
            return
        self.current_state = state
        self.use_drones = use_drones

        # Расширенный лог: сводка состояния и фаз (не чаще раза в 2 сек)
        if self.verbose:
            now_s = time.time()
            if now_s - self._last_vstate_ts >= 2.0:
                self._last_vstate_ts = now_s
                self._vlog(f"state: npc={len(state.npc_all)} targets={len(state.targets)} "
                           f"scan={len(state.scan_results or [])} "
                           f"drones={state.drones_in_bay}/{state.drones_in_space} "
                           f"loading={state.is_loading} docked={state.is_docked} "
                           f"warp={state.is_warp} menu={state.menu_open} "
                           f"local(h/n)={state.local_has_hostile}/{state.local_has_neutral} "
                           f"| mission={self._mission} step={self._combat_step} "
                           f"drones_phase={self._drone_phase}")

        # 0. Активация окна перед любым вводом
        if not self._activate_game_window():
            print(f"bot: no game window active")
            return # Если окно не найдено, ничего не делаем
        
        # 0a. ПРИОРИТЕТ: ЗАКРЫТЬ МОДАЛЬНОЕ ОКНО INFORMATION (блокирует весь ввод).
        #     Всплывает после варпа на аномалию ('Sansha Hidden Rally Point' и т.п.).
        #     Проще всего нажать Enter — у окна Information в фокусе кнопка Close
        #     (дефолтная). Если Enter не помог (модалка всё ещё висит), переходим
        #     к клику по кнопке Close (координаты даёт parse_modal_info).
        modal = getattr(state, 'modal_info', None) or {}
        if modal.get('open'):
            self._modal_attempts += 1
            if self._modal_attempts <= self.cfg['modal_enter_attempts']:
                # Первые попытки: Enter (универсальное закрытие Information-окна)
                pyautogui.press('enter')
                self._vlog(f"modal: pressed Enter (attempt {self._modal_attempts})")
            else:
                # Enter не помог — клик по кнопке Close (нижняя большая кнопка или верхняя иконка)
                close_btn = modal.get('close')
                if close_btn:
                    cx = close_btn['x'] + close_btn['w'] // 2
                    cy = close_btn['y'] + close_btn['h'] // 2
                    ox, oy = self._get_offset()
                    pyautogui.click(ox + cx, oy + cy)
                    self._vlog(f"modal: clicked Close @ ({cx},{cy}) (attempt {self._modal_attempts})")
                else:
                    # Кнопки Close нет — продолжаем жать Enter
                    pyautogui.press('enter')
                    self._vlog(f"modal: pressed Enter (no Close button, attempt {self._modal_attempts})")
            time.sleep(0.5)
            return
        # Модалки нет — сбрасываем счётчик попыток закрытия
        self._modal_attempts = 0
        
        # Статистика сессии: грубая эвристика "сайт зачищен" (до внедрения FSM)
        if state.npc_all:
            self._had_npcs = True
        elif self._had_npcs:
            self._had_npcs = False
            self.stats['sites_cleared'] += 1
            print(f"[Stats] Сайт зачищен (всего: {self.stats['sites_cleared']})")
        
        # 1. ПРИОРИТЕТ: КРИТИЧНЫЕ МОДУЛИ (репка и двигатель — кликом по иконке).
        #    Контроль в любом состоянии (после андока, перед варпом, в варпе,
        #    в бою) — кроме дока и экрана загрузки.
        off_x, off_y = self._get_offset()
        self._ensure_modules_on(state, off_x, off_y, time.monotonic())
        if self._stop_requested:
            return

        # 2. ПРИОРИТЕТ: БЕЗОПАСНОСТЬ
        if state.local_has_hostile or state.local_has_neutral:
            state.combat_mode_active = "SAFETY"
            self.emergency_exit("Враг в локале!")
            return

        # 3. БОЕВАЯ ЛОГИКА
        if self._stop_requested:
            return
        if state.npc_all:
            self.no_targets_timer = None
            state.combat_mode_active = "COMBAT"
            # NPC уже видны в овервью, но корабль ещё в варпе: захват цели
            # начинаем ТОЛЬКО после смены статуса (выхода из варпа)
            if state.is_warp:
                self._vlog_throttled('c_warpwait',
                                     "combat: NPC в овервью, но статус WARP — "
                                     "жду выхода из варпа")
                return
            # Мы прибыли на аномалию — фиксируем визит и начинаем бой
            if self._current_anom:
                self._visited_anoms[self._current_anom] = time.time()
                print(f"[Mission] Прибыли на '{self._current_anom}' — начинаю бой")
                self._reset_mission()
            self._combat_logic()
        else:
            # Целей нет — проверяем, нужно ли возвращать дронов
            self._handle_drones_recall()
            # Если все дроны собраны — фаза дронов и FSM цели сбрасываются
            if state.drones_in_space == 0:
                self._drone_phase = 'FIGHTING'
                self._combat_step = 'IDLE'
                self._lock_attempts = 0
            state.combat_mode_active = "SCANNING"
            # Миссия: выбрать аномалию из probe scanner и варпнуться на неё
            self._find_new_anomaly(state)

    def _ensure_modules_on(self, state, off_x, off_y, now):
        """Контроль критичных модулей: репка и двигатель (afterburner/MWD).

        Запускается на каждом тике в любом состоянии (после андока, перед
        варпом в аномалию, в варпе, в бою), КРОМЕ дока и экрана загрузки
        (там HUD-модулей нет). Репка держится включённой и в варпе (циклит),
        двигатель — только вне варпа: в варпе он бесполезен, EVE сама
        выключает его на входе в варп, включим после выхода.

        Модуль включается КЛИКОМ по его иконке в HUD: у средних и нижних
        слотов нет дефолтных хоткеев (F1..F8 только у высоких). Повторный
        клик защищён кулдауном — повторное нажатие ВЫКЛЮЧАЕТ модуль.

        Двигатель не включается обратно в окне prop_keep_off_time после
        его выключения перед варпом (см. _disable_engine) — иначе
        автоконтроль отменит подготовку к варпу.
        """
        if getattr(state, 'is_docked', False) or getattr(state, 'is_loading', False):
            self._was_docked = True
            return

        # Задача: контроль модуля ремонта, когда корабль НЕ в доке.
        #   - репка есть и READY -> включается выше (клик по иконке);
        #   - репки нет вообще -> предупреждение в лог: сразу после андока
        #     (переход docked -> undocked) и далее не чаще
        #     repair_missing_warn_interval секунд.
        repairers = getattr(state, 'repairers', {}) or {}
        is_docked = getattr(state, 'is_docked', False)
        if not repairers:
            undocked_now = self._was_docked and not is_docked
            if undocked_now or now - self._repair_missing_warn_ts >= self.cfg.get('repair_missing_warn_interval', 60.0):
                self._repair_missing_warn_ts = now
                print("[Bot][WARNING] Модуль ремонта не найден (REPAIR: NOT FOUND) — "
                      "проверь фит: чинить некому, контроль репки невозможен")
        self._was_docked = is_docked

        # 1. Репка — всегда включена (кроме дока, работает и в варпе)
        for name, status in (getattr(state, 'repairers', {}) or {}).items():
            if status != 'READY':
                continue
            if self._toggle_hud_module(state, name, off_x, off_y, now):
                print(f"[Bot] Репка {name} выключена — включаю (клик по иконке)")
                return   # один модуль за тик, остальное — на следующих тиках

        # 2. Двигатель (afterburner/MWD) — всегда включён вне варпа, КРОМЕ:
        #    - самого варпа/выхода на варп (is_warp/is_establishing);
        #    - окна после выключения двигателя перед варпом (_disable_engine).
        if getattr(state, 'is_warp', False) or getattr(state, 'is_establishing', False):
            return
        if now - self._prop_keep_off_ts < self.cfg['prop_keep_off_time']:
            self._vlog("modules: двигатель выключен перед варпом — не включаю обратно")
            return
        for name, status in (getattr(state, 'prop_modules', {}) or {}).items():
            if status != 'READY':
                continue
            if self._toggle_hud_module(state, name, off_x, off_y, now):
                print(f"[Bot] Двигатель {name} выключен — включаю (клик по иконке)")
                return

    def _disable_engine(self, state, off_x, off_y, now):
        """Выключить двигатель (afterburner/MWD) перед уходом в варп.

        Вызывается ДО открытия контекстного меню с warp-командой: клик по
        модулю HUD при открытом меню просто закроет меню. Успешное
        выключение открывает окно prop_keep_off_time, в течение которого
        _ensure_modules_on не включает двигатель обратно (статус в дампе
        обновляется с задержкой).
        """
        for name, status in (getattr(state, 'prop_modules', {}) or {}).items():
            if status != 'ACTIVE':
                continue
            if self._toggle_hud_module(state, name, off_x, off_y, now):
                self._prop_keep_off_ts = now
                print(f"[Bot] Двигатель {name} выключаю перед варпом (клик по иконке)")
                return True
        return False

    def _toggle_hud_module(self, state, slot_name, off_x, off_y, now):
        """Клик по иконке модуля в HUD по имени слота. True = клик сделан.

        Защита от двойного клика: повторный клик в пределах кулдауна не
        делается (иначе выключим только что включённый модуль — статус в
        дампе обновляется с задержкой).
        """
        last = self._module_click_ts.get(slot_name, 0.0)
        if now - last < self.cfg['key_repress_cooldown']:
            self._vlog(f"modules: {slot_name} — жду кулдаун клика")
            return False
        slot = self._find_slot_info(state, slot_name)
        if not slot or slot.get('x') is None:
            self._vlog_throttled(f"mcoord_{slot_name}",
                                 f"modules: {slot_name} — нет координат иконки в HUD")
            return False
        self._module_click_ts[slot_name] = now
        cx = (slot.get('x') or 0) + off_x
        cy = (slot.get('y') or 0) + off_y
        self._click_at(cx, cy)
        return True

    @staticmethod
    def _find_slot_info(state, slot_name):
        """Слот (с координатами иконки) по имени среди high/medium/low."""
        for slot_list in (getattr(state, 'high_slots', None),
                          getattr(state, 'medium_slots', None),
                          getattr(state, 'low_slots', None)):
            for m in slot_list or []:
                if m.get('name') == slot_name:
                    return m
        return None

    def _press_key_once(self, key, now):
        """Нажатие клавиши с защитой от двойного нажатия.

        Повторное нажатие того же хоткея в EVE ВЫКЛЮЧАЕТ модуль, поэтому
        одна и та же клавиша не нажимается чаще key_repress_cooldown секунд.
        """
        last = self._recent_keys.get(key, 0.0)
        if now - last < self.cfg['key_repress_cooldown']:
            return False
        self._recent_keys[key] = now
        pyautogui.press(key)
        return True

    def _combat_logic(self):
        if self._stop_requested:
            return
        state = self.current_state
        off_x, off_y = self._get_offset()
        now = time.monotonic()

        # Статистика: киллы (число захваченных целей уменьшилось в бою)
        self._track_kills(state)
        # Статистика: потери дронов / тревога "мало дронов"
        self._track_drones_state(state)

        # а) FSM ЦЕЛИ: выбор из овервью -> орбита -> лок -> атака
        self._target_fsm(state, off_x, off_y, now)

        # б) Focus fire среди захваченных целей (фрегаты-скрамеры первыми)
        if state.targets:
            self._focus_fire(state, off_x, off_y, now)

        # в) Атака до уничтожения цели: поддерживаем цикл оружия
        self._attack_until_dead(state, now)

        # г) ДРОНЫ: цикл выживания (только режим DRONES и только в бою)
        if self.use_drones and state.targets:
            self._drone_combat_cycle(state, now)

    # ==================== ЦЕЛИ: ПРИОРИТЕТЫ И ФОКУС ====================

    def _npc_class_map(self, state):
        """Имя записи овервью -> класс NPC ('FRIGATE', ...)."""
        cmap = {}
        for e in getattr(state, 'overview_entries', None) or []:
            name = str(e.get('name') or '').strip()
            cls = e.get('npc_class')
            if name and cls:
                cmap[name] = cls
        return cmap

    def _target_is_frigate(self, target, cmap):
        """Является ли захваченная цель NPC-фрегатом (скрам-угроза).

        Сопоставляем имя цели из панели лока с именами записей овервью,
        у которых парсер определил npc_class (см. full_parser.parse_overview_entry).
        """
        tname = str(target.get('name') or '').strip()
        if not tname:
            return False
        for oname, cls in cmap.items():
            if oname and (oname in tname or tname in oname) and cls == 'FRIGATE':
                return True
        return False

    def _lock_candidates(self, state):
        """Кандидаты на захват: сначала фрегаты (скрам-угроза), затем остальные.

        Координаты, заблокированные после неудачных попыток лока (_skip_coords),
        исключаются на 60 секунд.
        """
        now = time.time()
        skip = [c for c, ts in self._skip_coords.items() if now - ts < 60.0]
        frigates = [c for c in (state.npc_frigate or []) if c not in skip]
        rest = [c for c in (state.npc_all or [])
                if c not in frigates and c not in skip]
        return frigates + rest

    def _click_action(self, state, keyword, off_x, off_y):
        """Клик по кнопке действия в окне выбранного объекта (Approach/Orbit/...)."""
        item = state.selected_item or {}
        actions = item.get('actions') or []
        for a in actions:
            aname = str(a.get('action') or a.get('name') or '').lower()
            if keyword in aname:
                ax = (a.get('x') or 0) + off_x + (a.get('w') or 30) // 2
                ay = (a.get('y') or 0) + off_y + (a.get('h') or 30) // 2
                print(f"[Combat] Действие '{a.get('action')}' -> "
                      f"'{item.get('name') or '?'}' ({self._fmt_dist(item.get('distance'))})")
                self._click_at(ax, ay)
                return True
        print(f"[Combat] Действие '{keyword}' не найдено "
              f"({len(actions)} кнопок в окне выбранного объекта)")
        return False

    def _overview_row_coords(self, state, target_name=None):
        """Координаты строки овервью для орбиты хоткеем W+клик.

        Сначала ищем строку по имени атакуемой цели (overview_entries —
        координаты иконки строки, см. parse_overview), при неудаче берём
        последнюю выбранную строку (_current_target_coords).
        """
        tname = str(target_name or '').strip().lower()
        if tname:
            for e in getattr(state, 'overview_entries', None) or []:
                oname = str(e.get('name') or '').strip().lower()
                x, y = e.get('x'), e.get('y')
                if not oname or x is None or y is None:
                    continue
                if oname in tname or tname in oname:
                    return (float(x), float(y))
        return self._current_target_coords

    def _enable_orbit(self, state, off_x, off_y, target_name=None):
        """Включить орбиту вокруг цели.

        Если статус orbiting уже активен — команду НЕ дублируем: повторная
        команда орбиты сбрасывает текущую (корабль начинает кружить заново).

        Способы (по приоритету):
          1) иконка Orbit в окне выбранного объекта (клик курсором);
          2) хоткей W + клик по строке цели в овервью (W+LMB) — если иконки
             нет (окно выбранного объекта не распарсилось / кнопки нет).
        Цель: атакуемая (target_name) либо последняя выбранная строка.
        """
        if getattr(state, 'is_orbiting', False):
            self._vlog("orbit: статус orbiting уже активен — команду не дублирую")
            return True
        if self._click_action(state, 'orbit', off_x, off_y):
            return True
        row = self._overview_row_coords(state, target_name)
        if not row:
            print("[Combat] Орбиту включить нечем: нет иконки Orbit и координат цели")
            return False
        print(f"[Combat] Орбита через хоткей W + клик по строке овервью "
              f"({row[0]:.0f},{row[1]:.0f}) '{target_name or '?'}'")
        pyautogui.press('w')
        time.sleep(random.uniform(0.15, 0.35))
        self._click_at(row[0] + off_x, row[1] + off_y)
        return True

    def _ensure_orbit_on_target(self, state, off_x, off_y, now):
        """Контроль орбиты в бою: если орбита слетела — включить заново.

        Статус орбиты берём из HUD-индикации (state.is_orbiting, текст
        'Orbiting ...' над HUD, см. full_parser.parse_ui_root). Орбита должна
        быть вокруг атакуемой (активной) цели. Перепроверяем не чаще
        orbit_recheck_cooldown секунд — индикация обновляется с задержкой,
        и повторная команда не должна уходить каждый тик.
        """
        if state.is_orbiting:
            return
        if now - self._last_orbit_fix_ts < self.cfg['orbit_recheck_cooldown']:
            self._vlog_throttled('c_orbitwait', "combat: орбиты нет в индикации — жду перед повторным включением")
            return
        self._last_orbit_fix_ts = now
        active = state.active_target or (state.targets or [{}])[0]
        tname = str(active.get('name') or '') if isinstance(active, dict) else ''
        print(f"[Combat] Орбита выключилась — включаю заново вокруг "
              f"{tname or 'атакуемой цели'}")
        self._enable_orbit(state, off_x, off_y, target_name=tname)

    @staticmethod
    def _fmt_dist(meters):
        """Дистанция в человекочитаемом виде: '850 m' / '12.4 km'."""
        try:
            m = float(meters or 0.0)
        except (TypeError, ValueError):
            return "?"
        return f"{m:.0f} m" if m < 1000 else f"{m / 1000:.1f} km"

    def _attack_until_dead(self, state, now):
        """Атака до уничтожения цели: держим оружейные слоты активными.

        Пробегаем high slots (пушки/ракеты): если слот не циклит (не ACTIVE) —
        нажимаем его хоткей (F1..F8 по номеру слота). Работает в обоих режимах.
        Защита от двойного нажатия — через _press_key_once.
        """
        if not (self.cfg.get('auto_attack') and state.targets):
            return
        if now - self._last_attack_press < self.cfg['attack_cooldown']:
            return
        for slot in getattr(state, 'high_slots', None) or []:
            if slot.get('active'):
                continue   # слот уже циклит
            m = re.search(r'(\d+)', str(slot.get('name') or ''))
            if not m:
                continue
            key = f"f{int(m.group(1)) + self.cfg.get('slot_hotkey_offset', 0)}"
            if self._press_key_once(key, now):
                print(f"[Combat] Атака: активирую {slot.get('name')} ({key.upper()})")
                self._last_attack_press = now
            return   # один слот за тик

    def _focus_fire(self, state, off_x, off_y, now):
        """Focus fire: активной целью держим самую приоритетную из захваченных.

        Приоритет: NPC-фрегаты (носители скрамов) -> остальные по дистанции.
        Клик по строке цели в панели лока делает её активной.
        """
        cmap = self._npc_class_map(state)
        locked = list(state.targets or [])
        if not locked:
            return

        def _score(t):
            return (0 if self._target_is_frigate(t, cmap) else 1,
                    float(t.get('distance') or 0.0))
        focus = min(locked, key=_score)
        active = state.active_target
        if (active is None or focus is not active) and \
           now - self._last_lock_action_ts >= self.cfg['focus_switch_cooldown']:
            self._click_target_row(focus, off_x, off_y)
            self._last_lock_action_ts = now

    def _target_fsm(self, state, off_x, off_y, now):
        """Боевой цикл против NPC.

        Пока корабль в варпе FSM заморожена: цель уже может быть видна в
        овервью, но выбор/орбиту/лок начинаем только после выхода из варпа.

        Прилёт в аномалию -> выбор цели из овервью (одиночный клик) ->
        орбита выбранной цели (иконка Orbit, при её отсутствии хоткей
        W + клик по строке) -> попытка захвата в лок (Ctrl+клик) ->
        атака до уничтожения цели.

        Approach НЕ используется: сближение только орбитой вокруг цели.
        Если цель вне зоны захвата (лок не сработал за lock_timeout) —
        держим орбиту и повторяем лок; после lock_max_attempts неудачных
        попыток цель исключается (_skip_coords) и берётся следующая.

        В бою (ATTACK) контролируем статус орбиты: если она слетела —
        включаем заново вокруг атакуемой цели (см. _ensure_orbit_on_target).
        """
        # Во время варпа захват цели не начинаем
        if state.is_warp:
            self._vlog_throttled('fsm_warp', "combat: варп — захват цели отложен")
            return

        # Цели уже захвачены — атакуем (переход из любого состояния)
        if state.targets:
            if self._combat_step != 'ATTACK':
                self._combat_step = 'ATTACK'
                self._lock_attempts = 0
                # Дать индикации HUD время показать 'Orbiting ...'
                self._last_orbit_fix_ts = now
                print("[Combat] Цель захвачена! Атакую до уничтожения.")
            # Контроль орбиты: слетела — включаем заново вокруг атакуемой цели
            self._ensure_orbit_on_target(state, off_x, off_y, now)
            return

        if not state.npc_all:
            return

        step = self._combat_step

        if step in ('ATTACK', 'IDLE'):
            # 1. ВЫБОР цели из овервью (одиночный клик, приоритет: фрегаты)
            cand = self._lock_candidates(state)
            if not cand:
                return
            lx, ly = cand[0]
            self._current_target_coords = (lx, ly)
            print("[Combat] Выбираю цель из овервью (одиночный клик)")
            self._click_at(lx + off_x, ly + off_y)
            self._combat_step = 'SELECT'
            self._step_ts = now

        elif step == 'SELECT':
            if now - self._step_ts >= self.cfg['select_wait']:
                # 2. ОРБИТА выбранной цели (иконка Orbit, при её отсутствии
                #    хоткей W + клик по строке овервью)
                self._enable_orbit(state, off_x, off_y)
                self._combat_step = 'ORBIT'
                self._step_ts = now

        elif step == 'ORBIT':
            if now - self._step_ts >= self.cfg['orbit_wait']:
                # 3. ПОПЫТКА ЗАХВАТА В ЛОК (Ctrl+клик по строке овервью)
                cand = self._lock_candidates(state)
                if cand:
                    lx, ly = cand[0]
                    print("[Combat] Попытка захвата цели в лок (Ctrl+клик)")
                    pyautogui.keyDown('ctrl')
                    self._click_at(lx + off_x, ly + off_y)
                    pyautogui.keyUp('ctrl')
                    self._lock_attempts += 1
                    self._combat_step = 'LOCK'
                    self._step_ts = now

        elif step == 'LOCK':
            if now - self._step_ts >= self.cfg['lock_timeout']:
                if self._lock_attempts >= self.cfg['lock_max_attempts']:
                    print("[Combat] Лок так и не сработал — исключаю цель, беру следующую")
                    if self._current_target_coords:
                        self._skip_coords[self._current_target_coords] = time.time()
                    self._lock_attempts = 0
                    self._combat_step = 'IDLE'   # FSM выберет следующего кандидата
                else:
                    # Approach не используем (только орбита): держим орбиту
                    # и повторяем попытку лока
                    print(f"[Combat] Цель вне зоны захвата, попытка "
                          f"{self._lock_attempts}/{self.cfg['lock_max_attempts']} — "
                          f"держу орбиту, повторяю лок")
                    self._enable_orbit(state, off_x, off_y)
                    self._combat_step = 'ORBIT'
                    self._step_ts = now
            else:
                self._vlog_throttled('c_lock', f"combat: жду лок цели ещё "
                                               f"{self.cfg['lock_timeout'] - (now - self._step_ts):.1f} с "
                                               f"(попытка {self._lock_attempts}/"
                                               f"{self.cfg['lock_max_attempts']})")

    def _click_target_row(self, target, off_x, off_y):
        """Клик по строке цели в панели лока — делает её активной (фокус огня)."""
        tx = (target.get('x') or 0) + off_x
        ty = (target.get('y') or 0) + off_y
        dist = target.get('distance') or 0.0
        print(f"[Bot] Фокус огня -> {target.get('name') or 'Target'} ({dist:.0f} m)")
        self._click_at(tx, ty)

    def _click_at(self, x, y):
        """Клик по абсолютным координатам окна с рандомизацией (анти-детерминизм)."""
        # Смещение вправо от края иконки + разброс по Y и скорости
        target_x = x + random.randint(5, 30)
        target_y = y + random.randint(-2, 2)
        pyautogui.moveTo(target_x, target_y, duration=random.uniform(0.15, 0.4))
        time.sleep(random.uniform(0.1, 0.3))
        pyautogui.click()
        time.sleep(random.uniform(0.2, 0.5))

        # в) Дроны обрабатываются в _drone_combat_cycle (см. выше)

    # ==================== ДРОНЫ: ЦИКЛ ВЫЖИВАНИЯ ====================

    def _drone_combat_cycle(self, state, now):
        """Микро-FSM: FIGHTING -> RECALLING -> COOLDOWN -> FIGHTING.

        - Дронов бьют (или у дрона низкий HP) -> собираем (Shift+R).
          Пушки при этом продолжают стрелять.
        - Когда все дроны в ангаре -> пауза -> перезапуск (Shift+F) -> атака (F).
        """
        if self._drone_phase == 'FIGHTING':
            # 1. Дронов бьют — срочно собираем
            if state.drones_under_attack and state.drones_in_space > 0:
                print("[Bot][Drones] Дроны под огнём! Собираю (Shift+R), пушки продолжают")
                self._press_shift('r')
                self._drone_phase = 'RECALLING'
                return
            # 2. Низкий HP дрона (hp парсится не всегда: дефолт 100 -> не срабатывает)
            hp_threshold = self.cfg['drone_hp_recall'] * 100.0
            low = [d for d in (state.drone_entries or [])
                   if isinstance(d.get('hp'), (int, float)) and 0 < d['hp'] < hp_threshold]
            if low and state.drones_in_space > 0:
                names = ', '.join(str(d.get('name') or '?') for d in low)
                print(f"[Bot][Drones] Низкий HP: {names} — собираю (Shift+R)")
                self._press_shift('r')
                self._drone_phase = 'RECALLING'
                return
            # 3. Обычный боевой цикл: выпуск и атака
            if state.drones_in_bay > 0 and state.drones_in_space == 0:
                print("[Bot] Выпускаю дронов (Shift+F)")
                self._press_shift('f')
                time.sleep(5)  # Ждем, пока дроны выпустятся
            elif state.drones_in_space > 0 and not state.drones_fighting:
                print("[Bot] Дроны в атаку! (F)")
                pyautogui.press('f')
                time.sleep(1)

        elif self._drone_phase == 'RECALLING':
            if state.drones_in_space == 0:
                self._drone_phase = 'COOLDOWN'
                self._drone_cooldown_until = now + self.cfg['drone_relaunch_delay']
                print("[Bot][Drones] Все дроны в ангаре. Пауза перед перезапуском...")

        elif self._drone_phase == 'COOLDOWN':
            if now >= self._drone_cooldown_until:
                print("[Bot][Drones] Перезапускаю дронов в бой")
                self._drone_phase = 'FIGHTING'

    def _press_shift(self, key):
        """Shift+<key> (выпуск/сбор дронов)."""
        pyautogui.keyDown('shift')
        time.sleep(0.1)  # Даем игре время понять, что Shift зажат
        pyautogui.press(key)
        time.sleep(0.1)
        pyautogui.keyUp('shift')

    # ==================== СТАТИСТИКА СЕССИИ ====================

    def _track_kills(self, state):
        """Киллы: число захваченных целей уменьшилось, пока мы на сайте."""
        cur = len(state.targets or [])
        if cur < self._prev_locked_count and state.npc_all:
            killed = self._prev_locked_count - cur
            self.stats['kills'] += killed
            print(f"[Stats] Убито целей: +{killed} (всего: {self.stats['kills']})")
        self._prev_locked_count = cur

    def _track_drones_state(self, state):
        """Детект потери дронов и тревога 'мало дронов'."""
        total = (state.drones_in_bay or 0) + (state.drones_in_space or 0)
        if total <= 0:
            return
        if self._drone_baseline is None:
            self._drone_baseline = total
            return
        if total < self._drone_baseline:
            lost = self._drone_baseline - total
            self._drone_baseline = total
            self.stats['drones_lost'] += lost
            print(f"[!!!] ПОТЕРЯ ДРОНОВ: -{lost} (осталось {total}). "
                  f"Всего потеряно за сессию: {self.stats['drones_lost']}")
        elif total > self._drone_baseline:
            # Пополнение после дока — новая база
            self._drone_baseline = total
            self._low_drones_alerted = False

        if total < self.cfg['min_drones'] and not self._low_drones_alerted:
            self._low_drones_alerted = True
            print(f"[!!!] МАЛО ДРОНОВ: {total}. Нужен док и пополнение!")

    def _handle_drones_recall(self):
        """Логика сбора дронов, если целей нет более 10 секунд"""
        state = self.current_state
        
        # Если дроны в космосе и целей нет
        if state.drones_in_space > 0 and not state.npc_all:
            if self.no_targets_timer is None:
                self.no_targets_timer = time.time()
                print("[Bot] Целей нет, запустил таймер возврата дронов...")
            
            # Проверяем, прошло ли 10 секунд
            elapsed = time.time() - self.no_targets_timer
            self._vlog_throttled('d_recall', f"drones: целей нет {int(elapsed)} с, "
                                             f"в космосе {state.drones_in_space} "
                                             f"(сбор через {max(0, 10 - int(elapsed))} с)")
            if elapsed >= 10.0:
                if not state.drones_returning:
                    print(f"[Bot] Целей нет {int(elapsed)}с. Собираю дронов (Shift+R)")
                    self._press_shift('r')
                    time.sleep(5)  # Ждем, пока дроны придут
                    self.no_targets_timer = None # Сбрасываем таймер после команды
        else:
            self.no_targets_timer = None
    
    # ==================== МИССИЯ: ПОЛЁТ ПО АНОМАЛИЯМ (PROBE SCANNER) ====================

    def _anomaly_candidates(self, state):
        """Боевые аномалии из окна сканера, доступные для варпа.

        Фильтр по group+name (по умолчанию 'combat': группа 'Combat Site'
        и имена вида 'Sansha Den'); исключаем недавно посещённые.
        Координаты строк (x/y) считаются от окна игры (парсер накапливает).
        """
        now = time.time()
        flt = str(self.cfg.get('anomaly_group_filter') or '').lower()
        out = []
        for r in state.scan_results or []:
            name = str(r.get('name') or '')
            group = str(r.get('group') or '')
            if not name and not group:
                continue
            blob = f"{name} {group}".lower()
            if flt and flt not in blob:
                continue
            key = name or group
            if now - self._visited_anoms.get(key, 0) < self.cfg['anomaly_revisit_cooldown']:
                continue
            if r.get('x') is None or r.get('y') is None:
                continue   # строка без координат — кликать нечем
            out.append(r)
        return out

    def _pick_warp_menu_item(self, state):
        """Выбор warp-пункта в контекстном меню аномалии (по живому дампу).

        Реальное меню (raw_dump): подменю 'Within 0 m..100 km', пункты
        'Warp to Within 0 m' (прямой варп на 0!), 'Warp to Within' (подменю).

        Приоритет:
          1) пункт 'Warp to Within' с has_submenu — откроет список дистанций,
             бот выберет 'Within X km' из конфига (warp_distance_km);
          2) прямой пункт нужной дистанции ('... Within 20 km');
          3) любой другой warp-пункт, КРОМЕ варпа на 0 м — в боевых
             аномалиях варп-ин на 0 = мгновенное аггро NPC.
        """
        want = self.cfg['warp_distance_km']
        entries = [it for it in (state.menu_items or []) if it.get('type') == 'entry']

        # 1) ТОЧНО 'Warp to Within' БЕЗ прямой дистанции в названии
        #    ('Warp to Within 0 m' / 'Warp to Within 20 km' — прямой варп, его НЕ берём).
        #    В свежем дампе: 'Warp to Within' (подменю дистанций) vs 'Warp to Within 0 m'.
        for it in entries:
            txt = str(it.get('text') or '').strip().lower()
            if txt.startswith('warp to within'):
                rest = txt[len('warp to within'):].strip()
                if not rest or re.search(r'\d', rest):
                    continue   # прямое '0 m' / '(0 m)' / '20 km' — пропускаем
                return it      # сразу 'warp to within' (точное совпадение)
        # 1a) пункт 'Warp to Within' с подменю (если имя чуть другое)
        for it in entries:
            txt = str(it.get('text') or '').lower()
            if 'warp to within' in txt and not re.search(r'\d', txt) and it.get('has_submenu'):
                return it
        # 2) подменю дистанций: любой warp-пункт с подменю
        for it in entries:
            txt = str(it.get('text') or '').lower()
            if 'warp' in txt and it.get('has_submenu'):
                return it
        # 3) прямая нужная дистанция
        for it in entries:
            txt = str(it.get('text') or '').lower()
            if 'warp' in txt and f'within {want} km' in txt:
                return it
        # 4) любой warp, кроме варпа на 0 м
        for it in entries:
            txt = str(it.get('text') or '').lower()
            if 'warp' in txt and '0 m' not in txt and '(' not in txt:
                return it
        return None

    def _right_click_at(self, x, y):
        """ПКМ с рандомизацией (открытие контекстного меню)."""
        pyautogui.moveTo(x + random.randint(3, 20), y + random.randint(-2, 2),
                         duration=random.uniform(0.15, 0.4))
        time.sleep(random.uniform(0.1, 0.2))
        pyautogui.click(button='right')
        time.sleep(random.uniform(0.3, 0.6))

    def _reset_mission(self):
        self._mission = 'IDLE'
        self._mission_ts = 0.0
        self._warp_attempts = 0
        self._current_anom = None
        self._submenu_seen_ts = None

    def _find_new_anomaly(self, state):
        """Миссия: берём аномалию из списка окна Probe Scanner и летим на неё.

        Последовательность: ПКМ по строке аномалии -> в контекстном меню
        клик 'Warp to Location' -> если раскрылось подменю дистанций —
        выбор 'within X km' -> подтверждение варпа по индикации HUD
        (is_warp/is_establishing). Не подтвердился за таймаут — повтор;
        после warp_max_attempts неудач аномалия пропускается.
        """
        now = time.time()

        # Запрос STOP — миссию не продолжаем
        if self._stop_requested:
            return

        if state.is_docked or state.is_loading:
            self._vlog_throttled('m_dock', f"mission skip: docked={state.is_docked} "
                                           f"loading={state.is_loading}")
            return
        if state.drones_in_space > 0:
            self._vlog_throttled('m_drones', f"mission skip: дроны в космосе "
                                             f"({state.drones_in_space}) — ждём сбора")
            return   # сначала собираем дронов (_handle_drones_recall)

        # 1. Подтверждение варпа после клика
        if self._mission == 'WARP_CLICKED':
            if state.is_warp or state.is_establishing:
                print(f"[Mission] Варп на '{self._current_anom}' подтверждён")
                self._visited_anoms[self._current_anom] = now
                self._reset_mission()
                self._arrival_ts = now   # начало settle-паузы (овервью прогружается)
            elif now - self._mission_ts >= self.cfg['warp_confirm_timeout']:
                self._warp_attempts += 1
                if self._warp_attempts >= self.cfg['warp_max_attempts']:
                    print(f"[Mission] Варп не удался ({self._warp_attempts} попыток) — "
                          f"пропускаю '{self._current_anom}'")
                    self._visited_anoms[self._current_anom] = now
                    self._reset_mission()
                else:
                    print(f"[Mission] Варп не подтвердился — повтор "
                          f"(попытка {self._warp_attempts + 1})")
                    self._mission = 'IDLE'
            return

        # --- выбор аномалии и клики по меню ---
        if state.is_warp or state.is_establishing:
            self._vlog_throttled('m_warp', "mission skip: варп в процессе")
            return   # уже летим

        # Settle-пауза после варпа: NPC появляются на овервью с задержкой,
        # без неё бот улетит на следующую аномалию до того, как увидит крыс.
        if now - self._arrival_ts < self.cfg['arrival_settle_time']:
            self._vlog_throttled('m_settle', f"mission skip: settle ещё "
                                             f"{self.cfg['arrival_settle_time'] - (now - self._arrival_ts):.0f} с "
                                             f"(овервью прогружается)")
            return

        candidates = self._anomaly_candidates(state)
        if not candidates:
            self._vlog_throttled('m_empty', f"mission skip: боевых аномалий нет "
                                            f"(строк сканера: {len(state.scan_results or [])}, "
                                            f"фильтр='{self.cfg.get('anomaly_group_filter')}', "
                                            f"посещённых: {len(self._visited_anoms)})")
            return   # боевых аномалий в списке нет — ждём

        off_x, off_y = self._get_offset()

        if self._mission == 'IDLE':
            # Перед любым уходом в варп глушим двигатель. ВАЖНО: до открытия
            # меню — клик по модулю HUD при открытом контекстном меню просто
            # закроет меню, а не выключит модуль.
            self._disable_engine(state, off_x, off_y, now)
            anom = candidates[0]
            ax = (anom.get('x') or 0) + off_x + 40
            ay = (anom.get('y') or 0) + off_y + max(8, (anom.get('h') or 16) // 2)
            print(f"[Mission] Аномалия '{anom.get('name') or anom.get('id')}' "
                  f"({anom.get('group')}) — открываю меню (ПКМ по строке сканера)")
            self._right_click_at(ax, ay)
            self._current_anom = str(anom.get('name') or anom.get('id') or '?')
            self._mission = 'MENU_WAIT'
            self._mission_ts = now

        elif self._mission == 'MENU_WAIT':
            if state.menu_open and state.menu_items:
                # Стабилизация: меню «доезжает» до конечной позиции — если кликнуть
                # по координатам из дампа до этого, бот попадает мимо пунктов.
                if now - self._mission_ts < self.cfg.get('menu_settle', 0.8):
                    self._vlog_throttled('m_menwait',
                        f"mission: меню стабилизируется ещё "
                        f"{self.cfg.get('menu_settle', 0.8) - (now - self._mission_ts):.1f} с")
                else:
                    item = self._pick_warp_menu_item(state)
                    if item:
                        print(f"[Mission] Пункт меню '{item.get('text')}' — клик")
                        # Клик в ЦЕНТР строки (не в край) — надёжно попадает по тексту
                        self._click_at((item.get('x') or 0) + (item.get('w') or 160) // 2 + off_x,
                                       (item.get('y') or 0) + (item.get('h') or 22) // 2 + off_y)
                        if item.get('has_submenu'):
                            self._mission = 'SUBMENU_WAIT'
                        else:
                            self._mission = 'WARP_CLICKED'
                        self._mission_ts = now
                        self._submenu_seen_ts = None
                    else:
                        print("[Mission] Пункт Warp в меню не найден — закрываю меню")
                        pyautogui.press('esc')
                        self._mission = 'IDLE'
                        self._mission_ts = now
            elif now - self._mission_ts >= self.cfg['menu_open_timeout']:
                print("[Mission] Контекстное меню не открылось — повтор")
                self._mission = 'IDLE'
            else:
                self._vlog_throttled('m_menwait', f"mission: жду контекстное меню "
                                                  f"({now - self._mission_ts:.1f} с)")

        elif self._mission == 'SUBMENU_WAIT':
            # Подменю дистанций: ищем 'within 70 km' из конфига (или первую НЕ 0м 'within X km')
            want = self.cfg['warp_distance_km']
            target_item = None
            fallback_item = None
            for it in state.menu_items or []:
                if it.get('type') != 'entry':
                    continue
                txt = str(it.get('text') or '').lower()
                if 'within' not in txt:
                    continue
                if f"within {want} km" in txt:
                    target_item = it
                    break
                if 'km' in txt and '0 m' not in txt and fallback_item is None:
                    fallback_item = it   # запасной вариант дистанции (НЕ 0 м)
            item = target_item or fallback_item
            if item:
                # Стабилизация подменю: оно «выезжает» рядом с пунктом меню, и в
                # первые кадры координаты пунктов ещё не совпадают с экраном.
                if self._submenu_seen_ts is None:
                    self._submenu_seen_ts = now
                    self._vlog_throttled('m_seen',
                        f"mission: подменю дистанций появилось — жду стабилизации "
                        f"{self.cfg.get('submenu_settle', 1.0)} с")
                if now - self._submenu_seen_ts >= self.cfg.get('submenu_settle', 1.0):
                    print(f"[Mission] Дистанция варпа: '{item.get('text')}' — клик")
                    self._click_at((item.get('x') or 0) + (item.get('w') or 104) // 2 + off_x,
                                   (item.get('y') or 0) + (item.get('h') or 22) // 2 + off_y)
                    self._mission = 'WARP_CLICKED'
                    self._mission_ts = now
            else:
                # Пункт дистанции пропал из состояния — подменю могло закрыться/съехать
                self._submenu_seen_ts = None
                if now - self._mission_ts >= self.cfg['menu_open_timeout']:
                    # Подменю так и не поймали (варп мог пойти сразу) — ждём подтверждения
                    print("[Mission] Подменю дистанции не найдено — жду подтверждения варпа")
                    self._mission = 'WARP_CLICKED'
                    self._mission_ts = now
                else:
                    self._vlog_throttled('m_subwait', f"mission: жду подменю дистанций "
                                                      f"({now - self._mission_ts:.1f} с)")

    def emergency_exit(self, reason):
        self.stats['escapes'] += 1
        print(f"[!!!] EMERGENCY: {reason} (побегов за сессию: {self.stats['escapes']})")