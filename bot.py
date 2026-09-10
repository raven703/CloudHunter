import pyautogui
import pygetwindow as gw
import time
import random
import re
import ctypes

# --- Нативная отправка клавиш через win32 (keybd_event + VK-коды) ---
# Модуль keyboard не используется (проблемы с правами/хуками). VK-коды
# НЕ зависят от раскладки: pyautogui.press('w') на RU-раскладке маппит
# букву через VkKeyScan и не находит её, а keybd_event(0x57) жмёт
# физическую W всегда.
_USER32 = None
try:
    _USER32 = ctypes.windll.user32
except Exception:
    _USER32 = None  # не Windows — фолбэк на pyautogui

_KEYEVENTF_KEYUP = 0x0002


def _win_vk(key):
    """Символ/имя клавиши -> Windows Virtual-Key код (независимо от раскладки)."""
    k = str(key).lower()
    if len(k) == 1:
        if 'a' <= k <= 'z':
            return ord(k.upper())          # 'a'..'z' -> 0x41..0x5A
        if '0' <= k <= '9':
            return ord(k)                  # '0'..'9' -> 0x30..0x39
    named = {
        'enter': 0x0D, 'return': 0x0D, 'esc': 0x1B, 'escape': 0x1B,
        'tab': 0x09, 'space': 0x20, 'backspace': 0x08,
        'shift': 0x10, 'ctrl': 0x11, 'control': 0x11, 'alt': 0x12,
        'f1': 0x70, 'f2': 0x71, 'f3': 0x72, 'f4': 0x73, 'f5': 0x74,
        'f6': 0x75, 'f7': 0x76, 'f8': 0x77,
    }
    return named.get(k)


def _win_key_down(key):
    """Зажать клавишу нативно (win32). True = отправлено."""
    vk = _win_vk(key)
    if vk is None or _USER32 is None:
        return False
    try:
        _USER32.keybd_event(vk, 0, 0, 0)
        return True
    except Exception:
        return False


def _win_key_up(key):
    """Отпустить клавишу нативно (win32). True = отправлено."""
    vk = _win_vk(key)
    if vk is None or _USER32 is None:
        return False
    try:
        _USER32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
        return True
    except Exception:
        return False


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
            'drone_hp_check_interval': 2.0,# как часто проверять HP дронов (сек)
            # --- захват целей ---
            'lock_batch_size': 3,          # сколько целей захватывать за раз (если в овервью >= этого числа)
            'lock_batch_delay': 1.5,       # пауза между ctrl+кликами целей в пакете (сек) — клик должен успеть в тик сервера
            'ctrl_click_pause': 0.15,      # пауза Ctrl зажат -> клик -> отпустить (иначе EVE не видит модификатор)
            'warp_exit_settle': 2.5,       # пауза после выхода из варпа: статус корабля в дампе ещё "догоняет"
            'drone_launch_wait': 8.0,      # макс. ожидание выпуска дронов в шаге DRONES_LAUNCH (сек)
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
            'orbit_result_check_time': 3.0,    # через сколько сек проверить результат попытки включения орбиты
            'orbit_w_fallback_threshold': 2,   # после скольких неудач W+LMB переключиться на иконку Orbit
            'prop_keep_off_time': 25.0,         # окно "двигатель выключен перед варпом" (сек): меню варпа + старт варпа
            'repair_missing_warn_interval': 60.0,  # как часто предупреждать, если модуля ремонта нет (сек)
            # --- андок ---
            'undock_cooldown': 5.0,            # не кликать Undock чаще (сек), пока идёт андок
            'undock_timeout': 45.0,            # таймаут завершения андока (сек) — если статус не сменился, повторяем
            'undock_settle_time': 5.0,         # пауза после завершения андока (сек) — JSON ещё не обновился
            'probe_open_timeout': 5.0,         # не жать Alt+P чаще, пока окно открывается (сек)
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
        self._orbit_w_fails = 0          # счётчик неудачных попыток W+LMB орбиты (фолбэк на иконку)
        self._orbit_last_attempt_ts = 0.0  # ts последней попытки включения орбиты
        self._module_click_ts = {}       # слот -> ts последнего клика по модулю HUD (анти-даблклик)
        self._prop_keep_off_ts = 0.0     # ts выключения двигателя перед варпом (окно "не включать обратно")

        # --- Захват целей пакетами по N ---
        self._lock_queue = []            # очередь целей для захвата (кандидаты из _lock_candidates)
        self._lock_batch_remaining = 0   # сколько целей осталось захватить в текущем пакете
        self._lock_batch_ts = 0.0        # ts последнего ctrl+клика в пакете

        # --- Фокус: одна захваченная цель = атакуемая (пушки + дроны) ---
        self._focus_name = None          # имя цели в фокусе; меняется только после её уничтожения

        # --- Прилёт в аномалию: пауза на стабилизацию статуса после варпа ---
        self._just_exited_warp = False   # True в тике выхода из варпа -> ставим settle-паузу
        self._combat_start_ts = 0.0      # ts начала боевого пайплайна после варпа

        # --- Трекинг HP дронов (для детекта "дронов бьют") ---
        self._drone_hp_prev = {}         # drone_name -> hp (предыдущее значение)
        self._drone_hp_check_ts = 0.0    # ts последней проверки HP дронов

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
        self._warned_repair_missing = False   # алерт "репки нет" уже выводился (1 раз)
        self._warned_prop_missing = False     # алерт "двигателя нет" уже выводился (1 раз)

        # --- Старт: undock и проб-сканер ---
        self._last_undock_ts = 0.0       # последний клик Undock (анти-спам)
        self._undocking = False          # True после клика Undock — ждём завершения андока
        self._undock_start_ts = 0.0      # ts начала процедуры андока
        self._undock_complete_ts = 0.0   # ts завершения андока (is_docked сменился на False)
        self._undock_settle_until = 0.0  # ts окончания settle-паузы после андока (JSON обновляется)
        self._orbit_last_attempt_ts = 0.0  # ts последней попытки включения орбиты (для fallback)
        self._orbit_use_fallback = False   # True = использовать клик по иконке Orbit вместо W+LMB
        self._undock_settle_until = 0.0  # момент, до которого ждём после андока (settle-пауза)
        self._orbit_last_attempt_ts = 0.0 # ts последней команды орбиты (для детекта неудач)
        self._orbit_use_fallback = False   # переключатель: W+LMB не сработал -> используем иконку
        self._orbit_w_fails = 0           # счётчик неудач W+LMB (для переключения на иконку)
        self._orbit_pending_check = False # True после команды орбиты — ждём проверки результата
        self._orbit_check_ts = 0.0        # ts для отложенной проверки результата
        self._probe_open_ts = 0.0        # когда нажали Alt+P (анти-спам)
        self._probe_opening = False      # ждём, пока окно Probe Scanner откроется

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


    def _overview_row_coords(self, state, target_name=None):
        """Координаты строки цели в овервью для клика (W+LMB).

        Если target_name задан — ищем строку с таким именем.
        Иначе — последняя выбранная строка (_current_target_coords) или
        первая из npc_all.
        """
        # Сначала пробуем найти по имени в overview_entries
        if target_name:
            for e in getattr(state, 'overview_entries', None) or []:
                ename = str(e.get('name') or '').strip()
                if ename and (ename == target_name or ename in target_name or target_name in ename):
                    return (int(e.get('x') or 0), int(e.get('y') or 0))
        # Фолбэк: последняя выбранная строка
        if self._current_target_coords:
            return self._current_target_coords
        # Фолбэк: первая цель из npc_all
        if state.npc_all:
            return tuple(state.npc_all[0])
        return None

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
        """
        Главный тик бота. Пайплайн:
          1. Безопасность  + статус дока
          2. Открыть Probe Scanner (Alt+P, если закрыт)
          3. Проверить repair module  (вкл / алерт)
          4. Проверить двигатель     (алерт, если нет)
          5. Варп в аномалию        (двигатель ВЫКЛ перед варпом)
          6. Приземление -> бой     (репка + двигатель всегда вкл)
          7. Орбита: включить 1 раз, контролировать статус
          8. Стрельба по целям
        """
        # --- кооперативная остановка ---
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
        
        # 0a. Модалка Information (после варпа) — закрыть немедленно
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
        
        # ================================================================
        # ШАГ 1: БЕЗОПАСНОСТЬ + СТАТУС ДОКА
        # ================================================================
        if state.local_has_hostile or state.local_has_neutral:
            state.combat_mode_active = "SAFETY"
            self.emergency_exit("Враг в локале!")
            return
        if self._stop_requested:
            return

        off_x, off_y = self._get_offset()
        now_mono = time.monotonic()

        # Констатируем завершение андока: если андок был начат (_undocking=True)
        # и корабль больше не в доке — сбрасываем флаг, даже если is_docked
        # уже успел смениться между тиками. Запоминаем время завершения для settle-паузы.
        if self._undocking and not state.is_docked:
            self._undocking = False
            self._undock_complete_ts = now_mono
            print("[Mission] Андок завершён — корабль в космосе")

        # После андока ждём settle-паузу: JSON ещё не обновился, данные старые.
        # Без этой паузы бот читает устаревший JSON и принимает неверные решения.
        if self._undock_complete_ts > 0 and now_mono - self._undock_complete_ts < self.cfg['undock_settle_time']:
            self._vlog_throttled('m_undocksettle',
                                 f"undock: жду settle-паузу {self.cfg['undock_settle_time'] - (now_mono - self._undock_complete_ts):.1f}с")
            return

        if state.is_docked:
            state.combat_mode_active = "DOCKED"
            # Если андок уже идёт — ждём завершения, не кликаем повторно
            if self._undocking:
                if now_mono - self._undock_start_ts >= self.cfg['undock_timeout']:
                    # Таймаут — пробуем кликнуть снова
                    print(f"[Mission] Андок не завершился за {self.cfg['undock_timeout']}с — повторный клик")
                    self._undocking = False
                    self._last_undock_ts = 0.0  # сбрасываем кулдаун, чтобы кликнуть сразу
                else:
                    # Андок в процессе — ждём
                    self._vlog_throttled('m_undockwait',
                                         f"undock: жду завершения андока "
                                         f"({now_mono - self._undock_start_ts:.1f}с / "
                                         f"{self.cfg['undock_timeout']}с)")
            else:
                self._undock(state, off_x, off_y, now_mono)
            return
        if state.is_loading:
            state.combat_mode_active = "LOADING"
            return

        # ================================================================
        # ШАГ 2: ОТКРЫТЬ PROBE SCANNER (Alt+P, если закрыт)
        # ================================================================
        self._ensure_probe_scanner(state, now_mono)
        if self._stop_requested:
            return

        # ================================================================
        # ШАГ 3: ПРОВЕРИТЬ REPAIR MODULE (вкл / алерт если нет)
        # ================================================================
        # ================================================================
        # ШАГ 4: ПРОВЕРИТЬ ДВИГАТЕЛЬ (алерт если нет)
        # ================================================================
        if self._stop_requested:
            return
        self._ensure_modules_on(state, off_x, off_y, now_mono)
        if self._stop_requested:
            return

        # ================================================================
        # ШАГ 5: ВАРП В АНОМАЛИЮ (двигатель ВЫКЛ перед варпом)
        # ================================================================
        # ================================================================
        # ШАГ 6: Приземление -> бой (репка + двигатель всегда вкл)
        # ================================================================
        # ================================================================
        # ШАГ 7: Орбита (включить 1 раз, контролировать статус)
        # ================================================================
        # ================================================================
        # ШАГ 8: СТРЕЛЬБА ПО ЦЕЛЯМ
        # ================================================================
        if state.npc_all:
            # --- ШАГИ 6-8: ПРИЗЕМЛЕНИЕ -> БОЙ ---
            self.no_targets_timer = None
            state.combat_mode_active = "COMBAT"
            # NPC уже видны, но корабль ещё в варпе: ждём выхода из варпа.
            # Флаг нужен для settle-паузы в _target_fsm (think() выходит раньше,
            # чем FSM увидит варп, поэтому помечаем выход из варпа здесь).
            if state.is_warp:
                self._just_exited_warp = True
                self._vlog_throttled('c_warpwait',
                                     "combat: NPC в овервью, но статус WARP — "
                                     "жду выхода из варпа")
                return
            # Прибыли на аномалию — фиксируем визит и начинаем бой
            if self._current_anom:
                self._visited_anoms[self._current_anom] = time.time()
                print(f"[Mission] Прибыли на '{self._current_anom}' — начинаю бой")
                self._reset_mission()
            self._combat_logic()
        else:
            # --- ШАГ 5: ВАРП В АНОМАЛИЮ (двигатель ВЫКЛ перед варпом) ---
            self._handle_drones_recall()
            if state.drones_in_space == 0:
                self._drone_phase = 'FIGHTING'
                self._combat_step = 'IDLE'
                self._lock_attempts = 0
            state.combat_mode_active = "SCANNING"
            self._find_new_anomaly(state)

    def _undock(self, state, off_x, off_y, now_mono):
        """Шаг 1 (часть): андок из станции кликом по кнопке Undock в UI.

        Вызывается, когда корабль в доке и локал безопасен. Анти-спам:
        повторный клик не чаще undock_cooldown секунд (андок занимает время).
        После клика устанавливается флаг _undocking — бот ждёт смены статуса
        (is_docked -> False) в основном цикле think().
        """
        btn = getattr(state, 'undock_button', None)
        if not btn:
            self._vlog_throttled('m_no_undock',
                                 "undock: кнопка Undock не найдена в UI (ещё док?)")
            return
        if now_mono - self._last_undock_ts < self.cfg['undock_cooldown']:
            self._vlog("undock: жду кулдаун клика Undock")
            return
        self._last_undock_ts = now_mono
        self._undock_start_ts = now_mono
        self._undocking = True
        print(f"[Mission] В доке и безопасно — андок (клик Undock "
              f"@ {btn['x']},{btn['y']})")
        self._click_at((btn.get('x') or 0) + off_x, (btn.get('y') or 0) + off_y)

    def _ensure_probe_scanner(self, state, now_mono):
        """Шаг 2 пайплайна: открыть окно Probe Scanner (Alt+P), если его нет.

        Если окна нет и мы не в доке (нужны аномалии) — нажимаем Alt+P
        не чаще probe_open_timeout, ожидая появления окна в UI.
        """
        if getattr(state, 'probe_scanner_open', False):
            self._probe_opening = False
            return
        if not self._probe_opening:
            print("[Mission] Окно Probe Scanner закрыто — открываю (Alt+P)")
            pyautogui.hotkey('alt', 'p')
            self._probe_opening = True
            self._probe_open_ts = now_mono
            return
        # Окно ещё не появилось — ждём и по таймауту пробуем снова
        if now_mono - self._probe_open_ts >= self.cfg['probe_open_timeout']:
            self._probe_opening = False

    def _ensure_modules_on(self, state, off_x, off_y, now):
        """Шаги 3-4 пайплайна: контроль repair module и двигателя.

        РЕПКА: держится включённой всегда (кроме дока), в т.ч. в варпе
        (циклит). Если репки НЕТ на корабле — алерт в лог РОВНО 1 раз.

        ДВИГАТЕЛЬ: включается ТОЛЬКО в бою (state.npc_all). До боя и во
        время полёта к аномалии двигатель не включается (на старте
        любого полёта он выключен; перед варпом — выключается принудительно
        через _disable_engine). Если двигателя нет на корабле — алерт
        в лог РОВНО 1 раз.

        Модуль включается КЛИКОМ по его иконке в HUD: у средних/нижних слотов
        нет дефолтных хоткеев (F1..F8 только у высоких). Повторный клик
        защищён кулдауном — повторное нажатие ВЫКЛЮЧАЕТ модуль.
        """
        if getattr(state, 'is_docked', False) or getattr(state, 'is_loading', False):
            self._was_docked = True
            return

        repairers = getattr(state, 'repairers', {}) or {}
        prop_modules = getattr(state, 'prop_modules', {}) or {}

        # 1. Алерты об отсутствии критичных модулей (1 раз, не спамим)
        if not repairers:
            if not self._warned_repair_missing:
                self._warned_repair_missing = True
                print("[Bot][WARNING] Модуль ремонта не найден (REPAIR: NOT FOUND) — "
                      "проверь фит: чинить некому, контроль репки невозможен")
        if not prop_modules:
            if not self._warned_prop_missing:
                self._warned_prop_missing = True
                print("[Bot][WARNING] Двигатель (afterburner/MWD) не найден "
                      "(PROP: NOT FOUND) — проверь фит: нет propulsion-модуля")

        # 2. Репка — всегда включена (кроме дока, работает и в варпе)
        for name, status in repairers.items():
            if status != 'READY':
                continue
            if self._toggle_hud_module(state, name, off_x, off_y, now):
                print(f"[Bot] Репка {name} выключена — включаю (клик по иконке)")
                return   # один модуль за тик, остальное — на следующих тиках

        # 3. Двигатель (afterburner/MWD) — ТОЛЬКО в бою (корабль у цели).
        #    Вне боя / по пути к аномалии двигатель не включаем: EVE гасит
        #    его на входе в варп, а перед любым полётом он должен быть выключен.
        if not state.npc_all:
            return
        if getattr(state, 'is_warp', False) or getattr(state, 'is_establishing', False):
            return
        if now - self._prop_keep_off_ts < self.cfg['prop_keep_off_time']:
            self._vlog("modules: двигатель выключен перед варпом — не включаю обратно")
            return
        for name, status in prop_modules.items():
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

    def _press_key(self, key):
        """Нажать и отпустить клавишу (win32 VK — работает на любой раскладке)."""
        if _win_key_down(key):
            time.sleep(0.05)
            _win_key_up(key)
            return True
        try:
            pyautogui.press(key)
            return True
        except Exception:
            return False

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

    def _key_down_up(self, key):
        """Нажать и отпустить клавишу через win32 (VK-код keybd_event).

        VK-коды не зависят от раскладки: keybd_event(0x57) жмёт физическую W
        и на RU, и на EN. pyautogui.press('w') на RU-раскладке ищет букву 'w'
        через VkKeyScan, не находит её — нажатие теряется (орбита/дроны не
        срабатывали). Если win32 недоступен — фолбэк на pyautogui.
        """
        if _win_key_down(key):
            time.sleep(0.05)
            _win_key_up(key)
            return True
        # Fallback (не-Windows): pyautogui может терять буквы на RU-раскладке
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

        # а) ШАГ 6: FSM ЦЕЛИ: лок первой цели -> орбита -> дроны -> лок пакета
        self._target_fsm(state, off_x, off_y, now)

        # б) ШАГ 7: Фокус: одна захваченная цель = атакуемая (пушки + дроны);
        #    после её уничтожения — переключение на следующую захваченную
        if state.targets:
            self._focus_maintain(state, off_x, off_y, now)

        # в) ШАГ 8: Атака до уничтожения цели: поддерживаем цикл оружия
        self._attack_until_dead(state, now)

        # г) ДРОНЫ: цикл выживания (кроме фазы запуска — там запускает FSM)
        if self.use_drones and state.targets and self._combat_step != 'DRONES_LAUNCH':
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
        у которых парсер определил npc_class.
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

    def _enable_orbit(self, state, off_x, off_y, target_name=None, force=False):
        """Orbit: W+LMB primary, Orbit icon fallback.

        Метод:
          1) W + клик по строке цели в овервью (W+LMB) — основной метод.
              keyboard (VK-код 0x11): работает на любой раскладке (RU/EN).
          2) Если W+LMB не сработал N раз — переключаемся на клик по иконке Orbit.
          3) Если уже в орбите — не дублируем команду (кроме force=True:
             при смене цели фокуса орбиту нужно перевести на новую цель,
             даже если индикация is_orbiting ещё не обновилась).
        """
        if getattr(state, 'is_orbiting', False) and not force:
            self._vlog("orbit: already active")
            # Сбрасываем счётчик неудач — орбита активна
            self._orbit_w_fails = 0
            return True
        row = self._overview_row_coords(state, target_name)
        now = time.monotonic()
        # Фолбэк: если W не сработал N раз — используем иконку Orbit
        if self._orbit_use_fallback or self._orbit_w_fails >= self.cfg['orbit_w_fallback_threshold']:
            self._orbit_use_fallback = True
            if self._click_action(state, 'orbit', off_x, off_y):
                self._orbit_last_attempt_ts = now
                self._orbit_pending_check = True
                self._orbit_check_ts = now + self.cfg['orbit_result_check_time']
                return True
            return False
        if row:
            rx, ry = row
            print(f"[Combat] Orbit (W+click) around '{target_name or 'target'}'")
            # W через keyboard (VK-код) — работает на любой раскладке
            self._key_down_up('w')
            time.sleep(0.05)
            self._click_at(rx + off_x, ry + off_y)
            self._orbit_last_attempt_ts = now
            # Помечаем, что нужно проверить результат
            self._orbit_pending_check = True
            self._orbit_check_ts = now + self.cfg['orbit_result_check_time']
            return True
        # Нет строки — пробуем иконку
        if self._click_action(state, 'orbit', off_x, off_y):
            self._orbit_last_attempt_ts = now
            return True
        return False

    def _ensure_orbit_on_target(self, state, off_x, off_y, now):
        """Контроль орбиты в бою: если орбита слетела — включить заново.

        После команды орбиты проверяем результат: если через orbit_result_check_time
        орбита так и не включилась — увеличиваем счётчик неудач W+LMB. При
        достижении порога переключаемся на фолбэк (клик по иконке Orbit).
        """
        # Проверяем результат предыдущей попытки орбиты
        if self._orbit_pending_check and now >= self._orbit_check_ts:
            self._orbit_pending_check = False
            if not getattr(state, 'is_orbiting', False):
                self._orbit_w_fails += 1
                print(f"[Combat] Орбита НЕ включилась после W+LMB "
                      f"(неудача {self._orbit_w_fails}/{self.cfg['orbit_w_fallback_threshold']})")
                if self._orbit_w_fails >= self.cfg['orbit_w_fallback_threshold']:
                    self._orbit_use_fallback = True
                    print("[Combat] Переключаюсь на фолбэк: клик по иконке Orbit")
            else:
                # Орбита включилась — сбрасываем счётчик
                self._orbit_w_fails = 0

        if state.is_orbiting:
            return
        if now - self._last_orbit_fix_ts < self.cfg['orbit_recheck_cooldown']:
            self._vlog_throttled('c_orbitwait', "combat: орбиты нет в индикации — жду перед повторным включением")
            return
        self._last_orbit_fix_ts = now
        # Орбита — вокруг цели в ФОКУСЕ (атакуемой), а не любой активной
        focus_t = None
        if self._focus_name:
            for t in (state.targets or []):
                if str(t.get('name') or '') == self._focus_name:
                    focus_t = t
                    break
        active = focus_t or state.active_target or (state.targets or [{}])[0]
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

    def _focus_maintain(self, state, off_x, off_y, now):
        """Фокус: одна захваченная цель = атакуемая цель (пушки + дроны).

        Фокус ставится на первую захваченную цель (см. _target_fsm) и НЕ
        меняется, пока она жива — пушки и дроны бьют одну цель. Переключение
        только после уничтожения цели в фокусе: берём следующую захваченную
        (фрегаты приоритетнее как скрам-угроза), кликом по её строке делаем
        её активной, включаем орбиту вокруг неё и посылаем дроны (F).
        """
        locked = list(state.targets or [])
        if not locked:
            self._focus_name = None
            return
        names = [str(t.get('name') or '') for t in locked]
        if self._focus_name and self._focus_name in names:
            return  # фокус жив — целимся в ту же цель, ничего не делаем
        if now - self._last_lock_action_ts < self.cfg['focus_switch_cooldown']:
            return
        cmap = self._npc_class_map(state)

        def _score(t):
            return (0 if self._target_is_frigate(t, cmap) else 1,
                    float(t.get('distance') or 0.0))
        nxt = min(locked, key=_score)
        self._focus_name = str(nxt.get('name') or '')
        dist = float(nxt.get('distance') or 0.0)
        print(f"[Bot] Фокус -> {self._focus_name or 'Target'} ({dist:.0f} m) — "
              f"орбита + дроны")
        # Клик по строке цели в панели лока — делает её активной
        self._click_target_row(nxt, off_x, off_y)
        self._last_lock_action_ts = now
        # Орбита вокруг новой цели (force: индикация ещё показывает старую)
        self._enable_orbit(state, off_x, off_y,
                           target_name=self._focus_name or None, force=True)
        # Дроны атакуют новую цель фокуса
        if self.use_drones and state.drones_in_space > 0:
            print(f"[Bot] Дроны -> новая цель фокуса ({self._focus_name or 'Target'})")
            pyautogui.press('f')

    def _target_fsm(self, state, off_x, off_y, now):
        """Боевой цикл против NPC: пайплайн прилёта в аномалию.

        Пока корабль в варпе FSM заморожена. После выхода из варпа — пауза
        warp_exit_settle (статус корабля в дампе ещё обновляется), затем:

          1. LOCK_FIRST     — захват ПЕРВОЙ цели (Ctrl+LMB), она же фокус
          2. ORBIT_FIRST    — орбита вокруг ЭТОЙ ЖЕ цели (W+LMB)
          3. DRONES_LAUNCH  — выпуск дронов (Shift+F) и атака (F) цели фокуса
          4. LOCK_REST      — захват остальных целей пакета (Ctrl+LMB,
                              пауза lock_batch_delay между кликами)
          5. LOCK_WAIT      — ждём локи
          6. ATTACK         — бой; фокус = захваченная цель; после её
                              уничтожения переключение на следующую захваченную
                              (фокус -> орбита -> дроны F), см. _focus_maintain

        Сколько целей захватывать:
          - если в овервью >= lock_batch_size (3) — захватываем ровно 3
          - если меньше — все доступные
        Приоритет кандидатов: фрегаты (скрам-угроза) -> остальные по дистанции.
        Approach НЕ используется.
        """
        # Во время варпа захват цели не начинаем
        if state.is_warp:
            self._just_exited_warp = True
            self._vlog_throttled('fsm_warp', "combat: варп — захват цели отложен")
            return

        # Только что вышли из варпа: пауза на полное обновление статуса
        # корабля в дампе (is_warp/speed ещё "догоняют") — иначе клики мимо.
        if self._just_exited_warp:
            self._just_exited_warp = False
            self._combat_start_ts = now
            print(f"[Combat] Выход из варпа — жду стабилизации статуса "
                  f"({self.cfg['warp_exit_settle']:.0f} с) перед боем")
        if (self._combat_start_ts > 0 and
                now - self._combat_start_ts < self.cfg['warp_exit_settle']):
            self._vlog_throttled('c_arrive',
                                 f"combat: прилёт — жду стабилизации ещё "
                                 f"{self.cfg['warp_exit_settle'] - (now - self._combat_start_ts):.1f} с")
            return

        # Проверка результата попытки включения орбиты:
        # если прошло > orbit_result_check_time и орбита так и не включилась —
        # увеличиваем счётчик неудач W+LMB (фолбэк на иконку Orbit)
        if (not state.is_orbiting and self._orbit_last_attempt_ts > 0 and
                now - self._orbit_last_attempt_ts > self.cfg['orbit_result_check_time']):
            self._orbit_last_attempt_ts = 0.0  # сбрасываем, чтобы не считать повторно
            self._orbit_w_fails += 1
            print(f"[Combat] Орбита не включилась (попытка W+LMB #{self._orbit_w_fails})")

        # Контроль орбиты на боевых шагах с захваченными целями (кроме
        # ORBIT_FIRST/DRONES_LAUNCH — там команда орбиты только что была)
        if (state.targets and
                self._combat_step not in ('IDLE', 'ORBIT_FIRST', 'DRONES_LAUNCH')):
            self._ensure_orbit_on_target(state, off_x, off_y, now)

        step = self._combat_step

        if step == 'IDLE':
            if state.targets:
                # Локи уже есть (продолжение боя) — фокус на первую, атака
                self._focus_name = str((state.targets[0] or {}).get('name') or '')
                self._combat_step = 'ATTACK'
                self._lock_attempts = 0
                self._lock_batch_remaining = 0
                self._lock_queue = []
                self._last_orbit_fix_ts = now
                print(f"[Combat] {len(state.targets)} целей в локе — атакую.")
                return
            if not state.npc_all:
                return
            cand = self._lock_candidates(state)
            if not cand:
                return
            batch_size = min(self.cfg['lock_batch_size'], len(cand))
            self._lock_queue = list(cand[:batch_size])
            lx, ly = self._lock_queue[0]
            self._current_target_coords = (lx, ly)
            self._lock_attempts = 1
            print(f"[Combat] Захват цели 1/{len(self._lock_queue)} (Ctrl+клик) — "
                  f"она же цель фокуса")
            self._ctrl_click_at(lx, ly, off_x, off_y)
            self._combat_step = 'LOCK_FIRST'
            self._step_ts = now

        elif step == 'LOCK_FIRST':
            if state.targets:
                # Первый лок получен — эта цель становится фокусом
                self._focus_name = str((state.targets[0] or {}).get('name') or '')
                print(f"[Combat] Цель в локе: {self._focus_name or 'Target'} — фокус")
                self._combat_step = 'ORBIT_FIRST'
                self._step_ts = now
            elif not state.npc_all:
                self._lock_queue = []
                self._lock_attempts = 0
                self._combat_step = 'IDLE'
            elif now - self._step_ts >= self.cfg['lock_timeout']:
                if self._lock_attempts >= self.cfg['lock_max_attempts'] or not self._lock_queue:
                    print("[Combat] Первый лок не сработал — исключаю пакет, начинаю заново")
                    for c in self._lock_queue:
                        self._skip_coords[c] = time.time()
                    self._lock_queue = []
                    self._lock_attempts = 0
                    self._combat_step = 'IDLE'
                else:
                    self._lock_attempts += 1
                    lx, ly = self._lock_queue[0]
                    print(f"[Combat] Повтор захвата цели 1 "
                          f"({self._lock_attempts}/{self.cfg['lock_max_attempts']}, Ctrl+клик)")
                    self._ctrl_click_at(lx, ly, off_x, off_y)
                    self._step_ts = now

        elif step == 'ORBIT_FIRST':
            # Орбита вокруг ЭТОЙ ЖЕ захваченной цели (force: индикация is_orbiting
            # ещё может не обновиться, но команда нужна именно по цели фокуса)
            locked0 = (state.targets or [{}])[0]
            tname = str(locked0.get('name') or '') if isinstance(locked0, dict) else ''
            if not state.is_orbiting and \
               now - self._last_orbit_fix_ts >= self.cfg['orbit_recheck_cooldown']:
                print(f"[Combat] Орбита вокруг цели фокуса ({tname or 'Target'}, W+клик)")
                self._enable_orbit(state, off_x, off_y,
                                   target_name=tname or None, force=True)
            if now - self._step_ts >= self.cfg['orbit_wait']:
                self._combat_step = 'DRONES_LAUNCH'
                self._step_ts = now

        elif step == 'DRONES_LAUNCH':
            # Выпуск дронов и атака цели фокуса
            if not self.use_drones:
                self._combat_step = 'LOCK_REST'
                self._lock_batch_remaining = max(0, len(self._lock_queue) - 1)
                self._lock_batch_ts = now
            else:
                if state.drones_in_bay > 0 and state.drones_in_space == 0:
                    print("[Bot] Выпускаю дронов (Shift+F) — атакуют цель фокуса")
                    self._press_shift('f')
                    time.sleep(5)
                elif state.drones_in_space > 0 and not state.drones_fighting:
                    print(f"[Bot] Дроны в атаку на {self._focus_name or 'цель фокуса'}! (F)")
                    pyautogui.press('f')
                    time.sleep(1)
                launched = state.drones_in_space > 0 and state.drones_fighting
                if launched or now - self._step_ts >= self.cfg['drone_launch_wait']:
                    self._combat_step = 'LOCK_REST'
                    self._lock_batch_remaining = max(0, len(self._lock_queue) - 1)
                    self._lock_batch_ts = now

        elif step == 'LOCK_REST':
            # Захват остальных целей пакета (2-й, 3-й) с паузой между кликами
            if self._lock_batch_remaining > 0 and len(self._lock_queue) > 1:
                if now - self._lock_batch_ts >= self.cfg['lock_batch_delay']:
                    idx = len(self._lock_queue) - self._lock_batch_remaining
                    if idx < len(self._lock_queue):
                        lx, ly = self._lock_queue[idx]
                        print(f"[Combat] Захват цели {idx+1}/{len(self._lock_queue)} "
                              f"(Ctrl+клик)")
                        self._ctrl_click_at(lx, ly, off_x, off_y)
                        self._lock_batch_remaining -= 1
                        self._lock_batch_ts = now
            else:
                self._combat_step = 'LOCK_WAIT'
                self._step_ts = now

        elif step == 'LOCK_WAIT':
            locked_count = len(state.targets or [])
            if locked_count >= len(self._lock_queue):
                self._combat_step = 'ATTACK'
                self._lock_attempts = 0
                self._lock_queue = []
                self._last_orbit_fix_ts = now
                print(f"[Combat] Пакет захвачен ({locked_count} целей)! Атакую.")
            elif now - self._step_ts >= self.cfg['lock_timeout']:
                if locked_count > 0:
                    print(f"[Combat] Захвачено {locked_count} из "
                          f"{len(self._lock_queue)} — атакую тем, что есть")
                    self._combat_step = 'ATTACK'
                    self._lock_attempts = 0
                    self._lock_queue = []
                else:
                    print("[Combat] Локи слетели — новый заход по оставшимся NPC")
                    self._lock_queue = []
                    self._lock_attempts = 0
                    self._combat_step = 'IDLE'
            else:
                self._vlog_throttled('c_lock',
                                     f"combat: жду лок пакета ещё "
                                     f"{self.cfg['lock_timeout'] - (now - self._step_ts):.1f} с "
                                     f"(попытка {self._lock_attempts}/"
                                     f"{self.cfg['lock_max_attempts']})")

        elif step == 'ATTACK':
            if not state.targets:
                # Все захваченные цели уничтожены — новый пайплайн по оставшимся
                print("[Combat] Все захваченные цели уничтожены — новый заход по NPC")
                self._focus_name = None
                self._combat_step = 'IDLE'

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

    def _ctrl_click_at(self, lx, ly, off_x, off_y):
        """Захват цели: Ctrl + LMB клик по строке овервью с паузами.

        Ctrl зажимается ДО клика с паузой ctrl_click_pause — иначе EVE не
        успевает зарегистрировать модификатор и клик проходит как обычный
        (цель выделяется, но не захватывается в лок). Пауза после клика —
        чтобы захват успел обработаться до отпускания Ctrl / следующего
        действия. Без паузы между ctrl+кликами пакета захватываются не все
        цели (клик не попадает в тик сервера).
        """
        pyautogui.keyDown('ctrl')
        time.sleep(self.cfg['ctrl_click_pause'])
        self._click_at(lx + off_x, ly + off_y)
        time.sleep(self.cfg['ctrl_click_pause'])
        pyautogui.keyUp('ctrl')

    # ==================== ДРОНЫ: ЦИКЛ ВЫЖИВАНИЯ ====================

    def _check_drones_under_attack_by_hp(self, state, now):
        """Проверка: бьют ли дронов по снижению HP.

        Сравниваем текущий HP дронов с предыдущим значением. Если HP
        уменьшился — дрон под огнём, нужно собирать. Запоминаем текущие
        значения для следующей проверки.
        """
        if not state.drone_entries:
            self._drone_hp_prev.clear()
            return False

        # Проверяем не чаще интервала (HP обновляется с задержкой)
        if now - self._drone_hp_check_ts < self.cfg['drone_hp_check_interval']:
            # Но всё равно обновляем снимок HP для корректного сравнения позже
            for d in state.drone_entries:
                name = d.get('name')
                hp = d.get('hp')
                if name and isinstance(hp, (int, float)):
                    self._drone_hp_prev[name] = float(hp)
            return False

        self._drone_hp_check_ts = now
        hit_detected = False

        for d in state.drone_entries:
            name = d.get('name')
            hp = d.get('hp')
            if not name or not isinstance(hp, (int, float)):
                continue
            cur = float(hp)
            prev = self._drone_hp_prev.get(name, cur)
            if cur < prev - 0.5:  # HP уменьшился более чем на 0.5 — бьют
                hit_detected = True
            self._drone_hp_prev[name] = cur

        return hit_detected

    def _drone_combat_cycle(self, state, now):
        """Микро-FSM: FIGHTING -> RECALLING -> COOLDOWN -> FIGHTING.

        Контроль состояния дронов:
        1. Если дронов бьют (state.drones_under_attack или HP снижается) ->
           собираем (Shift+R). Пушки продолжают стрелять.
        2. Когда все дроны в ангаре (status in bay) -> пауза ->
           перезапуск (Shift+F) -> атака (F).
        3. Если дронов нет в ангаре и в космосе — алерт (дроны потеряны).
        """
        # Обновляем снимок HP и проверяем, бьют ли дронов
        hp_declining = self._drones_taking_damage(state, now)

        if self._drone_phase == 'FIGHTING':
            # 1. Дронов бьют — срочно собираем
            drones_attacked = (state.drones_under_attack or hp_declining) and state.drones_in_space > 0
            if drones_attacked:
                reason = "state.drones_under_attack" if state.drones_under_attack else "HP снижается"
                print(f"[Bot][Drones] Дроны под огнём ({reason})! Собираю (Shift+R)")
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
                print("[Bot][Drones] Перезапускаю дронов в бой (Shift+F)")
                self._press_shift('f')
                time.sleep(5)
                self._drone_phase = 'FIGHTING'

    def _press_shift(self, key):
        """Shift+<key> (выпуск/сбор дронов)."""
        pyautogui.keyDown('shift')
        time.sleep(0.1)  # Даем игре время понять, что Shift зажат
        pyautogui.press(key)
        time.sleep(0.1)
        pyautogui.keyUp('shift')

    def _drones_taking_damage(self, state, now):
        """Детект того, что дронов бьют (HP уменьшился).

        Сравниваем текущий HP каждого дрона с предыдущим значением. Если HP
        уменьшился — дрон под атакой. Проверяем не чаще drone_hp_check_interval
        (парсить HP дорого, а индикация обновляется с задержкой).
        """
        if now - self._drone_hp_check_ts < self.cfg['drone_hp_check_interval']:
            return []
        self._drone_hp_check_ts = now

        damaged = []
        for d in state.drone_entries or []:
            name = str(d.get('name') or '').strip()
            hp = d.get('hp')
            if not name or not isinstance(hp, (int, float)) or hp <= 0:
                continue
            prev = self._drone_hp_prev.get(name)
            if prev is not None and hp < prev:
                damaged.append({'name': name, 'hp': hp, 'prev': prev})
            self._drone_hp_prev[name] = hp
        return damaged

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
        """Шаг 5 пайплайна: берём аномалию из списка Probe Scanner и варимся.

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
            # Клик по имени аномалии (левая часть строки), НЕ по distance (правая часть)
            ax = (anom.get('x') or 0) + off_x + 10
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