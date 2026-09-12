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
# --- Отправка клавиш по физическому скан-коду (не зависит от раскладки) ---
# keybd_event(VK_W) на RU-раскладке уходит как русская 'Ц' — EVE ждёт
# физическую W для хоткея Orbit (W + ЛКМ). SendInput с KEYEVENTF_SCANCODE
# отправляет именно физическую клавишу, игнорируя активную раскладку.
_KEYEVENTF_SCANCODE = 0x0008

_SCANCODES = {
    'w': 0x11, 'a': 0x1E, 's': 0x1F, 'd': 0x20, 'q': 0x10,
    'e': 0x12, 'r': 0x13, 'f': 0x21, 'g': 0x22,
    'enter': 0x1C, 'space': 0x39, 'esc': 0x01,
    'shift': 0x2A, 'ctrl': 0x1D, 'alt': 0x38,
    'f1': 0x3B, 'f2': 0x3C, 'f3': 0x3D, 'f4': 0x3E,
    'f5': 0x3F, 'f6': 0x40, 'f7': 0x41, 'f8': 0x42,
}


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', ctypes.c_ushort),
        ('wScan', ctypes.c_ushort),
        ('dwFlags', ctypes.c_ulong),
        ('time', ctypes.c_ulong),
        ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [('type', ctypes.c_ulong), ('ki', _KEYBDINPUT)]


_INPUT_KEYBOARD = 1


def _send_scancode(scancode, up=False):
    """Отправить физический скан-код через SendInput (раскладка не влияет)."""
    if _USER32 is None:
        return False
    flags = _KEYEVENTF_SCANCODE | (_KEYEVENTF_KEYUP if up else 0)
    inp = _INPUT(
        type=_INPUT_KEYBOARD,
        ki=_KEYBDINPUT(wVk=0, wScan=scancode, dwFlags=flags, time=0,
                       dwExtraInfo=None),
    )
    try:
        sent = _USER32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        return sent == 1
    except Exception:
        return False


def _send_key_physical(key, up=False):
    """Отправить клавишу по физскан-коду. Fallback: VK через keybd_event."""
    k = str(key).lower()
    sc = _SCANCODES.get(k)
    if sc is None:
        return (_win_key_up(k) if up else _win_key_down(k))
    return _send_scancode(sc, up=up)


def _norm_name(s):
    """Нормализация имени NPC для сравнения: схлопнуть пробелы, casefold.

    Сравнения имён (фокус vs state.targets/overview_entries) не должны
    ломаться из-за различий в регистре или лишних пробелов.
    """
    return ' '.join(str(s or '').split()).casefold()


# --- Win32 константы для поиска окон ---
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_GW_OWNER = 4

try:
    _KERNEL32 = ctypes.windll.kernel32
except Exception:
    _KERNEL32 = None

class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ('dwSize', ctypes.c_ulong),
        ('cntUsage', ctypes.c_ulong),
        ('th32ProcessID', ctypes.c_ulong),
        ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
        ('th32ModuleID', ctypes.c_ulong),
        ('cntThreads', ctypes.c_ulong),
        ('th32ParentProcessID', ctypes.c_ulong),
        ('pcPriClassBase', ctypes.c_long),
        ('dwFlags', ctypes.c_ulong),
        ('szExeFile', ctypes.c_char * 260),
    ]

_WNDENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def _list_exefile_pids() -> set:
    """PID'ы всех процессов exefile.exe.

    Использует CreateToolhelp32Snapshot + Process32First/Next.
    Возвращает множество PID. На не-Windows — пустое множество.
    """
    if _KERNEL32 is None:
        return set()
    pids: set = set()
    snap = _KERNEL32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snap == _INVALID_HANDLE_VALUE or not snap:
        return pids
    try:
        entry = _PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
        ok = _KERNEL32.Process32First(snap, ctypes.byref(entry))
        while ok:
            exe = bytes(entry.szExeFile).split(b'\x00', 1)[0].lower()
            if exe == b'exefile.exe':
                pids.add(int(entry.th32ProcessID))
            ok = _KERNEL32.Process32Next(snap, ctypes.byref(entry))
    finally:
        _KERNEL32.CloseHandle(snap)
    return pids


def _enum_windows_for_pid(pid: int) -> list:
    """HWND'ы всех top-level окон, принадлежащих процессу pid.

    Возвращает список HWND (int). Фильтрует невидимые окна и
    owned-окна (у owned есть владелец — обычно служебные).
    """
    if _USER32 is None:
        return []
    result: list = []
    def _cb(hwnd, _lparam):
        wnd_pid = ctypes.c_ulong(0)
        _USER32.GetWindowThreadProcessId(
            ctypes.c_void_p(hwnd), ctypes.byref(wnd_pid))
        if wnd_pid.value != pid:
            return True
        if not _USER32.IsWindowVisible(ctypes.c_void_p(hwnd)):
            return True
        if _USER32.GetWindow(ctypes.c_void_p(hwnd), _GW_OWNER):
            return True
        result.append(int(hwnd))
        return True
    _USER32.EnumWindows(_WNDENUMPROC(_cb), 0)
    return result


def _get_window_title(hwnd: int) -> str:
    """Заголовок окна hwnd (UTF-16, до 512 символов)."""
    if _USER32 is None:
        return ''
    buf = ctypes.create_unicode_buffer(512)
    _USER32.GetWindowTextW(ctypes.c_void_p(hwnd), buf, 512)
    return buf.value


def _get_window_rect(hwnd: int) -> tuple:
    """(left, top, right, bottom) окна hwnd через GetWindowRect."""
    if _USER32 is None:
        return (0, 0, 0, 0)
    class _RECT(ctypes.Structure):
        _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                    ('right', ctypes.c_long), ('bottom', ctypes.c_long)]
    r = _RECT()
    if not _USER32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r)):
        return (0, 0, 0, 0)
    return (int(r.left), int(r.top), int(r.right), int(r.bottom))

# Заголовок окна EVE: 'EVE - <ИмяПерсонажа>'.
# Имя персонажа — 1..3 слова с заглавной буквы (латиница/кириллица),
# внутри слова допустимы буквы, цифры, '_', "'", '-'.
# Примеры валидных: 'EVE - Nikky Naari', 'EVE - O'Brien',
# 'EVE - Jean-Luc Picard', 'EVE - Alt'.
# Примеры невалидных: 'EVE - EVE Online' (Online — не имя),
# 'EVE - Launcher', 'EVE - ' (пусто).
_EVE_WINDOW_TITLE_RE = re.compile(
    r"^EVE\s+-\s+"
    r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_'\-]*"
    r"(?:\s+[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_'\-]*){0,2}"
    r"$"
)

# Явные исключения — служебные окна, а не персонажи.
_EVE_WINDOW_TITLE_BLACKLIST = {
    'eve online', 'launcher', 'client', 'eve launcher',
}


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
        # Одноразовые предупреждения об отсутствии оружия/дронов в фите
        self._warned_no_weapons = False
        self._warned_no_drones_fit = False
        # Небольшая пауза между действиями для стабильности.
        # 0.8 сек — катастрофа для боя: один клик занимает ~1.5-2 сек,
        # из-за чего не успевает орбита (W+клик), не срабатывает Ctrl+клик
        # (EVE не видит модификатор) и т.п. 0.02 сек — только защита от
        # случайных даблкликов.
        pyautogui.PAUSE = 0.02
        self.window_name: str | None = None
        # HWND выбранного окна EVE (Win32). Устанавливается в
        # set_window(). Используется в _get_offset/_activate_game_window
        # для точного доступа к конкретному окну, а не 'первому с
        # таким title' (pygetwindow возвращает дубликаты).
        self.window_hwnd: int | None = None
        self.no_targets_timer = None # Таймер для сбора дронов
        # Поправка на заголовок окна (если мажет по вертикали)
        self.CORRECTION_Y = 31

        # --- Конфиг боевого поведения (позже вынесем в config.json) ---
        self.cfg = {
            # --- бой: FSM цели ---
            'focus_switch_cooldown': 2.0,  # пауза между переключениями фокуса, сек
            'select_wait': 1.2,            # пауза после клика выбора цели, сек
            'entry_orbit_max_attempts': 5, # неудачных попыток сближения -> бан цели
            'entry_orbit_cooldown': 3.0,    # пауза между попытками сближения (сек)
            'debug_overview': True,         # отладочный вывод поиска координат овервью
            'orbit_wait': 2.5,             # пауза после команды орбиты до попытки лока, сек
            'lock_timeout': 15.0,          # ждать появления цели в панели лока, сек
            'lock_max_attempts': 3,        # неудачных попыток лока -> следующая цель
            # --- бой: атака ---
            'auto_attack': True,           # поддерживать цикл оружия (пушки/ракеты)
            'attack_cooldown': 4.0,        # мин. пауза между нажатиями хоткеев оружия, сек
            'key_repress_cooldown': 8.0,   # защита от двойного нажатия (выключит модуль)
            'slot_hotkey_offset': 0,       # inFlightHighSlot1 -> F1 (смещение нумерации)
            # --- дроны ---
            'drone_hp_recall': 0.5,        # доля HP дрона для отзыва (0..1; HP может не парситься)
            'drone_relaunch_delay': 3.0,   # пауза перед перезапуском собранных дронов, сек
            'drone_release_wait': 15.0,    # макс. ожидание attackingMe >=80% перед выпуском дронов, сек
            'min_drones': 1,               # тревога, если живых дронов меньше
            'drone_hp_check_interval': 2.0,# как часто проверять HP дронов (сек)
            # --- захват целей ---
            'lock_batch_size': 3,          # сколько целей захватывать за раз (если в овервью >= этого числа)
            'post_frigate_priority': 'battleship',  # 'battleship' | 'random' — порядок после фрегатов
            'lock_batch_delay': 0.5,       # пауза между ctrl+кликами пакета (сек) — клики идут одну за другой, БЕЗ ожидания захвата предыдущей
            'ctrl_click_pause': 0.3,      # пауза Ctrl зажат -> клик -> отпустить (иначе EVE не видит модификатор)
            'engage_orbit_hint_timeout': 2.0,  # сек ожидания orbit_hint_active
            'engage_w_hold_after_hint': 0.05,  # пауза после клика перед W up
            'engage_lock_pause': 0.2,          # пауза между орбитой и локом
            'entry_attempt_max': 3,           # после N неудач — бан цели
            'entry_retry_cooldown': 4.0,      # пауза между попытками
            'engage_repress_cooldown': 5.0,   # анти-спам engage
            'warp_exit_settle': 2.5,       # пауза после выхода из варпа: статус корабля в дампе ещё "догоняет"
            'drone_launch_wait': 8.0,      # макс. ожидание выпуска дронов в шаге DRONES_LAUNCH (сек)
            'drone_f_repress_cooldown': 6.0,  # повтор F, если дроны так и не атакуют цель (сек)
            'lockup_cooldown': 6.0,        # до-захват новых целей в бою не чаще (сек)
            'default_lock_range_km': 70,   # fallback-лимит лока, если QuickMessage ещё не видели (км)
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
            # --- побег на сейф при низком HP / враге в локале ---
            'local_control_enabled': True,     # вкл/выкл реакцию на hostile/neutral в локале
            'hp_warp_enabled': True,           # вкл/выкл отварп по HP
            'hp_warp_source': 'shield',        # 'shield' | 'armor' | 'min'
            'hp_warp_threshold': 40.0,        # % остатка, при котором улетаем
            'hp_warp_cooldown': 30.0,          # сек, не чаще одного отварпа по HP
            'safe_bookmark_dock_hint': 'dock',    # приоритетный спот: док-станция
            'safe_bookmark_name_hint': 'safe',    # запасной спот: Coordinate
            'safe_bookmark_system_only': True,    # только jumps == 0 (текущая система)
            'safe_warp_cooldown': 5.0,            # анти-спам ПКМ
        }

        # --- Микро-FSM дронов: FIGHTING | RECALLING | WAITING_ATTACK | COOLDOWN ---
        self._drone_phase = 'FIGHTING'
        self._drone_cooldown_until = 0.0
        self._drone_wait_start_ts = 0.0

        # --- FSM цели в бою: IDLE|SELECT|ORBIT|LOCK|ATTACK ---
        # Цикл: выбор цели из овервью -> орбита -> захват в лок -> атака.
        # Approach не используется: сближение только орбитой вокруг цели.
        self._combat_step = 'IDLE'
        self._step_ts = 0.0
        self._lock_attempts = 0
        self._current_target_coords = None
        self._skip_coords = {}           # имя цели, не поддающейся локу/сближению -> ts
        self._last_gun_press = 0.0
        self._last_drone_press = 0.0
        self._recent_keys = {}           # защита от двойного нажатия (выключит модуль)

        # --- Безопасность: отварп на сейф ---
        self._safety_active = False
        self._safety_reason = ''
        self._safety_started_ts = 0.0
        self._last_hp_warp_ts = 0.0
        self._last_safe_warp_ts = 0.0
        self._warned_no_bookmarks = False
        self._warned_no_safe = False
        self._last_orbit_fix_ts = 0.0    # последнее повторное включение орбиты (контроль в бою)
        self._module_click_ts = {}       # слот -> ts последнего клика по модулю HUD (анти-даблклик)
        self._prop_keep_off_ts = 0.0     # ts выключения двигателя перед варпом (окно "не включать обратно")

        # --- Захват целей пакетами по N ---
        self._lock_queue = []            # очередь целей для захвата (кандидаты из _lock_candidates)
        self._lock_batch_remaining = 0   # сколько целей осталось захватить в текущем пакете
        self._lock_batch_ts = 0.0        # ts последнего ctrl+клика в пакете

        # --- Фокус: одна захваченная цель = атакуемая (пушки + дроны) ---
        self._focus_name = None          # имя цели в фокусе; меняется только после её уничтожения
        self._last_focus_click_ts = 0.0  # ts последнего клика по строке фокуса (анти-спам)

        # --- Прилёт в аномалию: пауза на стабилизацию статуса после варпа ---
        self._just_exited_warp = False   # True в тике выхода из варпа -> ставим settle-паузу
        self._combat_start_ts = 0.0      # ts начала боевого пайплайна после варпа
        self._approach_target = None       # имя цели для сближения (прикрепляемся к одной)
        self._engage_pending = False       # True = ждём orbit_hint_active (фаза 2)
        self._engage_target_name = None    # имя цели, для которой ждём готовности
        self._engage_coords = None         # (x, y) цели
        self._engage_do_lock = True        # делать ли Ctrl+клик после орбиты
        self._engage_started_ts = 0.0      # ts начала фазы 1 (для таймаута)
        self._engage_last_ts = 0.0         # ts последнего engage (анти-спам)
        self._entry_attempts = 0           # счётчик неудачных сближений

        # --- Дроны: F отправляется ОДИН раз на цель фокуса ---
        self._drone_f_target = None      # имя цели, для которой F уже отправлен
        self._drone_f_sent_ts = 0.0      # ts последнего нажатия F на дронов
        self._last_lockup_ts = 0.0       # ts последнего до-захвата целей в бою

        # --- Трекинг HP дронов (для детекта "дронов бьют") ---
        self._drone_hp_prev = {}         # drone_name -> hp (предыдущее значение)
        self._drone_hp_check_ts = 0.0    # ts последней проверки HP дронов

        # --- Модальное окно Information (всплывает после варпа): закрытие Enter -> клик Close ---
        self._modal_attempts = 0

        # --- Миссия: полёт по аномалиям из probe scanner ---
        # IDLE -> MENU_WAIT (ПКМ по строке) -> SUBMENU_WAIT (дистанция) -> WARP_CLICKED
        self._safe_action = 'warp'   # 'dock' | 'warp' — что искать в меню SAFE
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
        self._entry_attempts = 0         # счётчик неудачных сближений с одной целью
        self._approach_target = None     # имя цели, к которой сближаемся
        self._engage_last_ts = 0.0       # ts последнего engage (анти-спам)
        self._probe_open_ts = 0.0        # когда нажали Alt+P (анти-спам)
        self._probe_opening = False      # ждём, пока окно Probe Scanner откроется

        # Статистика сессии: bot обновляет, GUI читает
        self.stats = stats if stats is not None else {
            'sites_cleared': 0, 'kills': 0, 'drones_lost': 0, 'escapes': 0,
        }

    def _find_hwnd_by_title(self, title: str) -> int | None:
        """HWND главного окна с указанным title среди exefile.exe.

        Идёт по PID exefile.exe → EnumWindows → сравнивает title.
        Если у PID несколько окон с одинаковым title (main + render) —
        берёт самое большое по площади (как в list_eve_windows).
        Возвращает HWND (int) или None.
        """
        if _USER32 is None or _KERNEL32 is None:
            return None
        want = str(title)
        best_hwnd: int | None = None
        best_area: int = -1
        for pid in _list_exefile_pids():
            for hwnd in _enum_windows_for_pid(pid):
                if _get_window_title(hwnd) != want:
                    continue
                left, top, right, bottom = _get_window_rect(hwnd)
                area = max(0, right - left) * max(0, bottom - top)
                if area > best_area:
                    best_area = area
                    best_hwnd = hwnd
        return best_hwnd

    def set_window(self, title: str) -> None:
        """Установить окно EVE для работы бота.

        Вызывается из GUI (main.py) после выбора пользователя.
        Находит HWND окна через Win32 (среди процессов exefile.exe)
        и сохраняет и title, и HWND. Если HWND не найден (окно
        закрылось, или не-Windows) — сохраняем только title и
        пишем предупреждение.
        """
        self.window_name = str(title)
        self.window_hwnd = None
        self.window_hwnd = self._find_hwnd_by_title(self.window_name)
        if self.window_hwnd:
            print(f"[Bot] Выбрано окно EVE: {self.window_name} "
                  f"(hwnd=0x{self.window_hwnd:X})")
        else:
            print(f"[Bot] Выбрано окно EVE: {self.window_name} "
                  f"(HWND не найден — работа через pygetwindow fallback)")

    def list_eve_windows(self) -> list:
        """Список окон EVE через Win32 API (по одному на процесс exefile.exe).

        pygetwindow возвращает дубликаты (у каждого процесса EVE есть
        дополнительное render-окно с тем же title). Здесь мы идём от
        процессов exefile.exe к их окнам через EnumWindows —
        гарантированно одно главное окно на процесс.

        Возвращает тот же формат, что раньше:
          [{'title': str, 'left': int, 'top': int,
            'width': int, 'height': int, 'is_active': bool}, ...]
        Отсортирован по title.
        """
        result: list = []
        if _USER32 is None or _KERNEL32 is None:
            print("[Bot] list_eve_windows: Win32 API недоступен")
            return result

        pids = _list_exefile_pids()
        if not pids:
            return result

        for pid in pids:
            hwnds = _enum_windows_for_pid(pid)
            # Оставляем только окна с заголовком EVE - <Имя>
            candidates = []
            for hwnd in hwnds:
                title = _get_window_title(hwnd)
                if not _EVE_WINDOW_TITLE_RE.match(title):
                    continue
                name_part = title[len('EVE - '):].strip().lower()
                if name_part in _EVE_WINDOW_TITLE_BLACKLIST:
                    continue
                left, top, right, bottom = _get_window_rect(hwnd)
                width = max(0, right - left)
                height = max(0, bottom - top)
                candidates.append({
                    'title': title,
                    'left': left,
                    'top': top,
                    'width': width,
                    'height': height,
                    'is_active': bool(_USER32.GetForegroundWindow() == hwnd),
                    '_hwnd': hwnd,     # внутреннее поле, в GUI не нужно
                    'pid': pid,
                })
            if not candidates:
                continue
            # Если у процесса несколько подходящих окон — берём самое
            # большое по площади (главное, не render).
            best = max(candidates, key=lambda w: w['width'] * w['height'])
            # Убираем внутреннее поле _hwnd из возвращаемого словаря.
            best.pop('_hwnd', None)
            result.append(best)

        result.sort(key=lambda x: x['title'])
        return result

    def _get_offset(self):
        """Экранные координаты окна игры для клика (left, top+CORRECTION_Y).

        Приоритет:
          1) self.window_hwnd — через Win32 GetWindowRect (точно то окно,
             которое выбрал пользователь);
          2) self.window_name — через pygetwindow (fallback, менее
             надёжно: может вернуть render-окно с тем же title);
          3) ничего нет — (0, 0).
        """
        if getattr(self, 'window_hwnd', None):
            try:
                left, top, _right, _bottom = _get_window_rect(
                    self.window_hwnd)
                if left or top or _right or _bottom:
                    return int(left), int(top) + self.CORRECTION_Y
            except Exception as e:
                print(f"[Bot] _get_offset: GetWindowRect failed: {e}")
        if not self.window_name:
            return 0, 0
        try:
            windows = gw.getWindowsWithTitle(self.window_name)
        except Exception:
            return 0, 0
        if windows:
            win = windows[0]
            return int(win.left), int(win.top) + self.CORRECTION_Y
        return 0, 0

    def _activate_game_window(self) -> bool:
        """Выводит окно EVE на передний план.

        Приоритет:
          1) self.window_hwnd — SetForegroundWindow(hwnd);
          2) self.window_name — pygetwindow.activate() (fallback);
          3) ничего — False.
        """
        if getattr(self, 'window_hwnd', None):
            try:
                ok = bool(_USER32.SetForegroundWindow(
                    ctypes.c_void_p(self.window_hwnd)))
                if ok:
                    return True
            except Exception as e:
                print(f"[Bot] SetForegroundWindow failed: {e}")
        if not self.window_name:
            print("[Bot] Окно EVE не выбрано — активация невозможна")
            return False
        try:
            windows = gw.getWindowsWithTitle(self.window_name)
        except Exception as e:
            print(f"[Bot] Ошибка при активации окна: {e}")
            return False
        if not windows:
            print("[Bot] Ошибка: Окно EVE не найдено!")
            return False
        win = windows[0]
        if not win.isActive:
            print(f"[Bot] Активирую окно: {win.title}")
            try:
                win.activate()
                time.sleep(0.5)
            except Exception as e:
                print(f"[Bot] Ошибка при активации окна: {e}")
                return False
        return True


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
        
        # Статистика сессии: эвристика "сайт зачищен" — боевые NPC
        # (npc_class != None), а не любые записи овервью (станции/врата
        # не считаются: иначе "зачистка" не зафиксируется на сайтах,
        # где в овервью остались врата/станция).
        has_npcs_now = self._has_hostile_npcs(state)
        if has_npcs_now:
            self._had_npcs = True
        elif self._had_npcs:
            self._had_npcs = False
            self.stats['sites_cleared'] += 1
            print(f"[Stats] Сайт зачищен (всего: {self.stats['sites_cleared']})")
        
        # ================================================================
        # БЕЗОПАСНОСТЬ + СТАТУС ДОКА
        # ================================================================
        # Обработка миссии "отварп на сейф" в первую очередь (SAFE_MENU_WAIT
        # обрабатывается внутри _find_new_anomaly).
        if self._mission == 'SAFE_MENU_WAIT':
            self._find_new_anomaly(state)
            return

        # Контроль локала можно отключить (local_control_enabled=False) —
        # тогда бот НЕ уходит в сейф при hostile/neutral в локале, но
        # HP-защита (hp_warp) продолжает работать.
        hostile = (self.cfg.get('local_control_enabled', True)
                   and (state.local_has_hostile or state.local_has_neutral))
        low_hp = self._is_low_hp(state)

        if hostile or low_hp:
            state.combat_mode_active = "SAFETY"
            reason = "Враг в локале!" if hostile else "Низкий HP"
            if not self._safety_active:
                self._safety_active = True
                self._safety_reason = 'hostile' if hostile else 'low_hp'
                self._safety_started_ts = time.monotonic()
                self.stats['escapes'] += 1
                if low_hp:
                    self._last_hp_warp_ts = time.monotonic()
                print(f"[!!!] SAFETY: {reason} — отход "
                      f"(побегов за сессию: {self.stats['escapes']})")
                self._warp_to_safe(state)
            return
        else:
            if self._safety_active:
                print("[Bot] SAFETY: угроза устранена — продолжаю работу")
                self._safety_active = False
                self._safety_reason = ''

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
        # ШАГ 1b: ОРУЖИЕ/ДРОНЫ — бот сам определяет, чем атаковать
        # ================================================================
        # use_drones больше не приходит из GUI — считаем по фиту корабля.
        has_guns = bool(getattr(state, 'high_slots', None))
        has_drones = (state.drones_in_bay + state.drones_in_space) > 0
        self.use_drones = has_drones
        self.use_guns = has_guns
        if not has_guns and not has_drones and not self._warned_no_weapons:
            self._warned_no_weapons = True
            print("[Bot][WARNING] В фите нет ни пушек, ни дронов — бот не сможет атаковать")
            return  # ничего не делаем дальше (кроме безопасности выше)

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
        # ШАГ 8: СТРЕЛБА ПО ЦЕЛЯМ
        # ================================================================
        # Боевые NPC — только те, у которых npc_class непусто (FRIGATE/
        # CRUISER/...). Станции, врата, планеты, wreck — не боевые цели,
        # с ними _combat_logic бессмысленна: лок не пройдёт, а бот будет
        # стоять в ожидании вечно.
        if self._has_hostile_npcs(state):
            # --- ШАГИ 6-8: ПРИЗЕМЛЕНИЕ -> БОЙ ---
            self.no_targets_timer = None
            state.combat_mode_active = "COMBAT"
            # NPC уже видны, но корабль ещё в варпе: ждём выхода из варпа.
            # Флаг нужен для settle-паузы в _combat_loop (think() выходит раньше,
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
                self._entry_attempts = 0
                self._approach_target = None
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
        """Нажать и отпустить клавишу по физическому скан-коду.

        SendInput + KEYEVENTF_SCANCODE отправляет ФИЗИЧЕСКУЮ клавишу, игнорируя
        активную раскладку: W остаётся W и на RU, и на EN. Это критично для
        хоткея Orbit (W + ЛКМ) — EVE матчит физическую клавишу, а не VK-код.

        Fallback (скан-кода нет или SendInput недоступен): win32 VK, затем
        pyautogui.
        """
        if _send_key_physical(key, up=False):
            time.sleep(0.05)
            _send_key_physical(key, up=True)
            return True
        if _win_key_down(key):
            time.sleep(0.05)
            _win_key_up(key)
            return True
        try:
            pyautogui.press(key)
        except Exception:
            pass
        return True

    def _combat_logic(self):
        if self._stop_requested:
            return
        state = self.current_state
        off_x, off_y = self._get_offset()
        now = time.monotonic()
        self._track_kills(state)
        self._track_drones_state(state)
        self._combat_loop(state, off_x, off_y, now)

    # ==================== ЦЕЛИ: ПРИОРИТЕТЫ И ФОКУС ====================

    def _ensure_targets_locked(self, state, off_x, off_y, now):
        """Гарантировать наличие хотя бы одной захваченной цели.

        Возвращает:
          True  — есть хотя бы одна захваченная цель (state.targets непуст).
          False — захваченных целей нет; бот начнёт захват заново.

        Если захваченных целей нет и кандидатов в овервью тоже нет — просто
        возвращает False (боту нечего делать, ждём появления NPC).
        """
        if state.targets:
            return True

        # Уже идёт процесс захвата — не мешаем.
        if self._combat_step in ('LOCK_WAIT', 'ORBIT_ENTRY',
                                  'ORBIT_FIRST', 'DRONES_LAUNCH'):
            return False

        cand = self._lock_candidates(state)
        if not cand:
            self._vlog_throttled('t_no_targets',
                "combat: нет захваченных целей и нет кандидатов в овервью")
            return False

        # Сброс FSM только из IDLE/ATTACK — начать захват заново.
        if self._combat_step != 'IDLE':
            print(f"[Combat] Нет захваченных целей, но {len(cand)} кандидатов — "
                  f"перезапуск захвата (сброс FSM)")
            self._combat_step = 'IDLE'
            self._lock_queue = []
            self._lock_attempts = 0
            self._lock_batch_remaining = 0
            self._focus_name = None
            self._entry_attempts = 0
            self._approach_target = None
        return False

    def _npc_class_map(self, state):
        """Имя NPC -> класс из overview_entries.npc_class.

        Используется в _focus_maintain для приоритизации фрегатов.
        (Раньше метод ошибочно назывался _lock_candidates — второй def
        с тем же именем затенял его, а вызов _npc_class_map падал
        с AttributeError: 'EveBot' object has no attribute '_npc_class_map'.)
        """
        cmap = {}
        for e in getattr(state, 'overview_entries', None) or []:
            name = str(e.get('name') or '').strip()
            cls = e.get('npc_class')
            if name and cls:
                cmap[name] = cls
        return cmap

    def _has_hostile_npcs(self, state):
        """Есть ли в овервью хоть один NPC (не станция/врата/астероид).

        Использует поле npc_class из parse_overview_entry: оно
        заполняется по _texturePath иконки ('npcfrigate_16.png' -> FRIGATE)
        или fallback-классификацией по имени (pithi, guristas, ... -> NPC).
        Если npc_class непустой — это NPC. Станции/врата/астероиды
        получают type='station'/'stargate'/... и npc_class=None.

        Возвращает True, если есть хотя бы один NPC в овервью.
        Если парсер не пишет npc_class (старая версия) — fallback на
        npc_all (счётчик NPC из parse_npc_lists).
        """
        env_types = ('station', 'stargate', 'planet', 'asteroid', 'citadel', 'wormhole')
        # 1) Основной путь: npc_class в записи овервью.
        has_npc_class = False
        for e in (getattr(state, 'overview_entries', None) or []):
            if e.get('npc_class'):
                return True
            # Пометим, что поле вообще присутствует (даже None) —
            # чтобы отличить "парсер не пишет npc_class" от "npc_class=None".
            if 'npc_class' in e:
                has_npc_class = True
            # Fallback: если npc_class is None, но type не экологический —
            # считаем это потенциальной целью (NPC без иконки в дампе).
            if e.get('npc_class') is None and str(e.get('type') or '').lower() not in env_types:
                if e.get('name'):
                    return True

        # 2) Fallback: если парсер не пишет npc_class, но npc_all непусто —
        # значит есть NPC (parse_npc_lists нашёл брекеты).
        if not has_npc_class and getattr(state, 'npc_all', None):
            return True
        return bool(getattr(state, 'npc_all', None))

    def _npc_distances(self, state):
        """Список (name, distance_m, x, y) по непустым overview_entries.

        Дистанция берётся из overview_entries (там уже парсится
        parse_distance). Это ЕДИНСТВЕННЫЙ источник дистанции до цели.
        Пропускаем записи без имени или координат.
        Исключаем только явно не-NPC объекты (станции/врата/планеты/астероиды).
        Исключаем цели в бане (_skip_coords — не поддающиеся сближению).
        """
        env_types = ('station', 'stargate', 'planet', 'asteroid', 'citadel', 'wormhole')
        now = time.time()
        skip = {name for name, ts in self._skip_coords.items() if now - ts < 60.0}
        out = []
        for e in getattr(state, 'overview_entries', None) or []:
            name = str(e.get('name') or '').strip()
            if not name:
                continue
            # Исключаем только явно не-NPC объекты — врата, станции и т.п.
            # Раньше фильтр был if not e.get('npc_class'), что отбрасывало
            # NPC без иконки (когда SpaceObjectIcon отсутствует в дампе).
            if str(e.get('type') or '').lower() in env_types:
                continue
            dist = float(e.get('distance') or 0.0)
            x = e.get('x')
            y = e.get('y')
            if x is None or y is None:
                continue
            # Исключаем цели в бане (не поддающиеся сближению)
            if name.lower() in skip:
                continue
            out.append((name, dist, float(x), float(y)))
        return out

    def _target_is_frigate(self, target, cmap):
        tname = str(target.get('name') or '').strip()
        if not tname:
            return False
        for oname, cls in cmap.items():
            if oname and (oname in tname or tname in oname) and cls == 'FRIGATE':
                return True
        return False

    def _lock_candidates(self, state):
        """Кандидаты на захват из overview_entries (имя + координаты + класс).

        Возвращает список словарей:
          {'name': str, 'x': float, 'y': float,
           'distance': float, 'npc_class': Optional[str]}

        Приоритет: фрегаты (скрам-угроза) -> остальные по дистанции.
        Координаты, помеченные как "не поддающиеся локу" (_skip_coords),
        исключаются на 60 секунд.

        Лимит дистанции лока:
          * state.lock_range_km — если клиент уже показывал QuickMessage
            "It must be within X km" в этой сессии;
          * cfg['default_lock_range_km'] — fallback, если QuickMessage
            ещё не видели (до первой неудачной попытки лока клиент
            не публикует лимит в UI);
          * запас 5% (умножение на 0.95) — компенсирует задержку
            между моментом чтения дистанции и серверной проверкой лока.
        """
        now = time.time()
        skip = {n.lower() for n, ts in self._skip_coords.items() if now - ts < 60.0}

        limit_km = getattr(state, 'lock_range_km', None)
        if not limit_km:
            limit_km = self.cfg.get('default_lock_range_km', 70)
        limit_m = float(limit_km) * 1000.0 * 0.95   # 5% запас

        frigates, battleships, rest = [], [], []
        for e in (getattr(state, 'overview_entries', None) or []):
            name = str(e.get('name') or '').strip()
            if not name:
                continue
            # Только боевые NPC. Исключаем явно не-NPC объекты.
            # Раньше фильтр был if not e.get('npc_class'), что отбрасывало
            # NPC без иконки (когда SpaceObjectIcon отсутствует в дампе).
            env_types = ('station', 'stargate', 'planet', 'asteroid', 'citadel', 'wormhole')
            if str(e.get('type') or '').lower() in env_types:
                continue
            x, y = e.get('x'), e.get('y')
            if x is None or y is None:
                continue
            coord = (float(x), float(y))
            if name.lower() in skip:
                continue
            dist = float(e.get('distance') or 0.0)
            if limit_m and dist > 0 and dist > limit_m:
                continue
            item = {
                'name': name,
                'x': coord[0],
                'y': coord[1],
                'distance': dist,
                'npc_class': e.get('npc_class'),
            }
            cls = e.get('npc_class')
            if cls == 'FRIGATE':
                frigates.append(item)
            elif cls == 'BATTLESHIP':
                battleships.append(item)
            else:
                rest.append(item)

        # Внутри каждого списка — ближайший первым
        frigates.sort(key=lambda c: c['distance'])
        battleships.sort(key=lambda c: c['distance'])
        rest.sort(key=lambda c: c['distance'])

        # Приоритет: фрегаты -> баттлшипы -> остальные
        # (post_frigate_priority='random' перемешивает баттлшипы с остальными)
        # Диагностика: почему _lock_candidates пусто. Печатаем при verbose
        # раз в 3 сек (троттлинг), чтобы понять, что отсекает цели.
        total_ov = len(getattr(state, 'overview_entries', None) or [])
        skipped = 0
        for e in (getattr(state, 'overview_entries', None) or []):
            x, y = e.get('x'), e.get('y')
            if x is None or y is None:
                continue
            if (float(x), float(y)) in skip:
                skipped += 1
        self._vlog_throttled(
            'lock_cand_debug',
            f"lock_candidates: ov={total_ov} skip={len(skip)} "
            f"(в бане: {skipped}) limit={limit_m/1000:.1f}km "
            f"-> fr={len(frigates)} bs={len(battleships)} rest={len(rest)}",
            interval=3.0)

        if self.cfg.get('post_frigate_priority') == 'random':
            after = battleships + rest
            random.shuffle(after)
            return frigates + after
        return frigates + battleships + rest

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
        self._vlog_throttled(
            f'click_action_notfound_{keyword}',
            f"Действие '{keyword}' не найдено "
            f"({len(actions)} кнопок в окне выбранного объекта)",
            interval=5.0)
        return False

    def _overview_row_coords(self, state, target_name=None):
        """Координаты строки овервью для орбиты хоткеем W+клик.

        Сначала ищем строку по имени атакуемой цели (overview_entries —
        координаты иконки строки, см. parse_overview), при неудаче берём
        последнюю выбранную строку (_current_target_coords).
        """
        tname = str(target_name or '').strip().lower()
        entries = list(getattr(state, 'overview_entries', None) or [])
        if tname:
            # Сначала точное совпадение
            for e in entries:
                oname = str(e.get('name') or '').strip().lower()
                x, y = e.get('x'), e.get('y')
                if oname and x is not None and y is not None and oname == tname:
                    return (float(x), float(y))
            # Отладка: если не нашли — логируем что есть в entries
            if self.cfg.get('debug_overview', False):
                names = [str(e.get('name') or '').strip().lower() for e in entries]
                print(f"[Debug] _overview_row_coords: target={tname!r} not found in {names}")
            # Потом частичное (startswith — чтобы не ловить 'Centii Enslaver II' при поиске 'Centii Enslaver')
            for e in entries:
                oname = str(e.get('name') or '').strip().lower()
                x, y = e.get('x'), e.get('y')
                if not oname or x is None or y is None:
                    continue
                if oname.startswith(tname + ' ') or tname.startswith(oname + ' '):
                    return (float(x), float(y))
        return self._current_target_coords

    def _engage_target(self, state, off_x, off_y, target_name=None,
                       coords=None, do_lock=True):
        """Фаза 1: выделить цель ЛКМ, зажать W, выйти.

        Клик ЛКМ по строке овервью открывает SelectedItemWnd, а W down
        активирует в EVE режим "Orbit — Click target". Клик по цели
        (фаза 2) делается в _check_engage_ready, когда EVE подтвердит
        готовность через state.orbit_hint_active.

        Возвращает True, если W зажата и ждём фазу 2.
        False — если координат нет или уже идёт фаза 2.
        """
        if self._engage_pending:
            self._vlog_throttled('engage_pending',
                "engage: уже ждём готовности EVE (фаза 2)")
            return False

        if coords is None:
            coords = self._overview_row_coords(state, target_name)
        if not coords:
            self._vlog_throttled('engage_no_coords',
                f"engage: нет координат для '{target_name}'")
            return False

        rx, ry = coords
        sx, sy = rx + off_x, ry + off_y
        print(f"[Combat] Engage '{target_name or 'target'}' @ ({int(sx)},{int(sy)}) "
              f"— выделяю, жму W")

        # 1. Выделить цель ЛКМ — открыть SelectedItemWnd
        pyautogui.moveTo(sx, sy, duration=0.15)
        time.sleep(0.1)
        pyautogui.click()
        time.sleep(self.cfg.get('select_wait', 0.8))

        # 2. Зажать W физическим scancode (RU-safe)
        _send_key_physical('w', up=False)

        # 3. Запомнить состояние фазы 2
        self._engage_pending = True
        self._engage_target_name = target_name
        self._engage_coords = (rx, ry)
        self._engage_do_lock = do_lock
        self._engage_started_ts = time.monotonic()
        return True

    def _check_engage_ready(self, state, off_x, off_y):
        """Фаза 2: ждём orbit_hint_active от EVE, кликаем по цели, лок.

        Вызывается в начале _combat_logic. Если фаза 1 не активна —
        ничего не делает. Если таймаут — отпускает W и сбрасывает.
        """
        if not self._engage_pending:
            return

        now = time.monotonic()
        # Таймаут: EVE не подтвердила за engage_orbit_hint_timeout
        if now - self._engage_started_ts > self.cfg['engage_orbit_hint_timeout']:
            _send_key_physical('w', up=True)
            self._engage_pending = False
            target = self._engage_target_name
            self._engage_target_name = None
            self._engage_coords = None
            print(f"[Combat] Engage '{target}' — EVE не подтвердила за "
                  f"{self.cfg['engage_orbit_hint_timeout']:.1f}с")
            return

        # EVE ещё не готова — ждём
        if not getattr(state, 'orbit_hint_active', False):
            self._vlog_throttled('engage_wait_hint',
                f"engage: жду 'Click target' от EVE "
                f"({now - self._engage_started_ts:.1f}с)")
            return

        # EVE готова — кликаем по цели (W+клик)
        target = self._engage_target_name
        coords = self._engage_coords
        if not coords:
            _send_key_physical('w', up=True)
            self._engage_pending = False
            self._engage_target_name = None
            return
        rx, ry = coords
        sx, sy = rx + off_x, ry + off_y
        pyautogui.moveTo(sx, sy, duration=0.1)
        time.sleep(0.05)
        pyautogui.click()
        time.sleep(self.cfg.get('engage_w_hold_after_hint', 0.05))
        _send_key_physical('w', up=True)
        print(f"[Combat] Engage '{target}' — W+клик отправлен (орбита)")

        # Лок: Ctrl+клик по той же цели
        if self._engage_do_lock:
            time.sleep(self.cfg.get('engage_lock_pause', 0.2))
            pyautogui.moveTo(sx, sy, duration=0.1)
            time.sleep(0.05)
            with pyautogui.hold('ctrl'):
                time.sleep(0.15)
                pyautogui.click()
                time.sleep(0.15)
            print(f"[Combat] Engage '{target}' — Ctrl+клик отправлен (лок)")

        self._engage_pending = False
        self._engage_target_name = None
        self._engage_coords = None
        self._engage_last_ts = now

    @staticmethod
    def _fmt_dist(meters):
        """Дистанция в человекочитаемом виде: '850 m' / '12.4 km'."""
        try:
            m = float(meters or 0.0)
        except (TypeError, ValueError):
            return "?"
        return f"{m:.0f} m" if m < 1000 else f"{m / 1000:.1f} km"

    def _attack_until_dead(self, state, now):
        """Активация оружейных слотов (F1..F8), если цель фокуса не атакована.

        Работает ТОЛЬКО в фазе ATTACK. Если под целью УЖЕ есть индикатор
        targetedByMeIndicator — не жмём повторно. Кулдаун раздельный от дронов.
        """
        if self._combat_step != 'ATTACK':
            return
        if not (self.cfg.get('auto_attack') and state.targets):
            return
        if not self._focus_name:
            return

        # Цель фокуса должна быть в локах и активна.
        focus_locked = any(
            _norm_name(t.get('name')) == _norm_name(self._focus_name)
            for t in (state.targets or [])
        )
        if not focus_locked:
            return
        if not state.active_target or \
                _norm_name(state.active_target.get('name')) != _norm_name(self._focus_name):
            return

        # Если под целью УЖЕ есть индикатор атаки — не жмём повторно.
        if self._target_is_being_attacked(state, self._focus_name):
            return

        if now - self._last_gun_press < self.cfg['attack_cooldown']:
            return

        for slot in getattr(state, 'high_slots', None) or []:
            if slot.get('active'):
                continue
            m = re.search(r'(\d+)', str(slot.get('name') or ''))
            if not m:
                continue
            key = f"f{int(m.group(1)) + self.cfg.get('slot_hotkey_offset', 0)}"
            if self._press_key_once(key, now):
                print(f"[Combat] Атака: активирую {slot.get('name')} ({key.upper()})")
                self._last_gun_press = now
            return   # один слот за тик

    def _target_is_being_attacked(self, state, target_name):
        """Проверка: атакуем ли мы цель (targetedByMeIndicator в овервью).

        В дампе под целью в овервью есть Sprite '_name': 'targetedByMeIndicator'
        (текстура activeTarget.png). Парсер сохраняет это в overview_entries
        как поле 'targeted_by_me'.
        """
        if not target_name:
            return False
        tname = str(target_name).strip().lower()
        for e in (getattr(state, 'overview_entries', None) or []):
            oname = str(e.get('name') or '').strip().lower()
            if not oname:
                continue
            if oname == tname or oname in tname or tname in oname:
                return bool(e.get('targeted_by_me'))
        return False

    # ==================== БЕЗОПАСНОСТЬ: ОТВАРП НА СЕЙФ ====================

    def _is_low_hp(self, state) -> bool:
        """Пора ли улетать на сейф по HP.

        Смотрим shield/armor по настройке hp_warp_source. Не срабатываем,
        если уже в SAFETY, в варпе, в доке, или недавно отварпали по HP.
        """
        if not self.cfg.get('hp_warp_enabled'):
            return False
        if state.is_docked or state.is_loading:
            return False
        if state.is_warp or state.is_establishing:
            return False
        if self._safety_active and self._safety_reason == 'low_hp':
            return False
        if time.monotonic() - self._last_hp_warp_ts < self.cfg['hp_warp_cooldown']:
            return False

        src = self.cfg.get('hp_warp_source', 'shield')
        shield = state.shield_pct if isinstance(state.shield_pct, (int, float)) else None
        armor = state.armor_pct if isinstance(state.armor_pct, (int, float)) else None

        if src == 'shield':
            hp = shield
        elif src == 'armor':
            hp = armor
        else:
            vals = [v for v in (shield, armor) if v is not None]
            hp = min(vals) if vals else None

        if hp is None:
            return False
        return hp <= float(self.cfg['hp_warp_threshold'])

    def _find_safe_bookmark(self, state):
        """Найти спот для отварпа/докинга в букмарках.

        Приоритет:
          1. Док-станция (Citadel / Refinery / Station) с jumps==0 и именем,
             содержащим safe_bookmark_dock_hint ('dock').
          2. Любая док-станция с jumps==0.
          3. Coordinate с jumps==0 и именем, содержащим safe_bookmark_name_hint.
          4. Любой Coordinate с jumps==0.
          5. Любой спот с jumps==0.

        Возвращает dict {label, type, jumps, x, y, h} или None.
        """
        bms = getattr(state, 'bookmarks', None) or []
        if not bms:
            return None
        system_only = self.cfg.get('safe_bookmark_system_only', True)
        dock_hint = str(self.cfg.get('safe_bookmark_dock_hint', 'dock')).lower()
        safe_hint = str(self.cfg.get('safe_bookmark_name_hint', 'safe')).lower()

        dock_types = ('citadel', 'refinery', 'station', 'engineering complex',
                     'astrahus', 'fortizar', 'keepstar', 'tatara', 'raitaru',
                     'athanor', 'azbel', 'sotiyo')

        def _is_dock(b):
            t = str(b.get('type') or '').lower()
            return t in dock_types

        def _is_coordinate(b):
            return str(b.get('type') or '').lower() == 'coordinate'

        def _in_system(b):
            return (not system_only) or (b.get('jumps', 0) == 0)

        # 1. Док-станция с dock_hint в имени
        for b in bms:
            if _in_system(b) and _is_dock(b) and \
                    dock_hint in str(b.get('label') or '').lower():
                return b
        # 2. Любая док-станция
        for b in bms:
            if _in_system(b) and _is_dock(b):
                return b
        # 3. Coordinate с safe_hint в имени
        for b in bms:
            if _in_system(b) and _is_coordinate(b) and \
                    safe_hint in str(b.get('label') or '').lower():
                return b
        # 4. Любой Coordinate
        for b in bms:
            if _in_system(b) and _is_coordinate(b):
                return b
        # 5. Любой спот
        for b in bms:
            if _in_system(b):
                return b
        return None

    def _warp_to_safe(self, state) -> None:
        """ПКМ по споту букмарка → Dock (для док-станции) или Warp (для Coordinate).

        ПКМ по букмарку автоматически закроет любое открытое меню — EVE
        сама переключает контекст. В SAFE_MENU_WAIT затем выбирается
        'Dock' (если спот — станция) или 'Warp to Within 0 m'.
        """
        if not getattr(state, 'bookmarks', None):
            if not self._warned_no_bookmarks:
                self._warned_no_bookmarks = True
                print("[Bot][WARNING] Окно букмарков не найдено или пусто — "
                      "отход невозможен. Открой окно Places.")
            return

        safe = self._find_safe_bookmark(state)
        if not safe:
            if not self._warned_no_safe:
                self._warned_no_safe = True
                print("[Bot][WARNING] В букмарках нет спота с jumps=0 "
                      "(текущая система) — отход невозможен.")
            return

        now = time.monotonic()
        if now - self._last_safe_warp_ts < self.cfg['safe_warp_cooldown']:
            return
        self._last_safe_warp_ts = now

        off_x, off_y = self._get_offset()
        bx = int(safe['x'] + off_x + 24)
        by = int(safe['y'] + off_y + safe.get('h', 22) // 2)

        # Что будем искать в меню: Dock для станции, Warp для Coordinate.
        stype = str(safe.get('type') or '').lower()
        dock_types = ('citadel', 'refinery', 'station', 'engineering complex',
                     'astrahus', 'fortizar', 'keepstar', 'tatara', 'raitaru',
                     'athanor', 'azbel', 'sotiyo')
        if stype in dock_types:
            action = 'dock'
        else:
            action = 'warp'

        print(f"[Safety] ПКМ по споту '{safe.get('label')}' "
              f"(jumps={safe.get('jumps')}, type={safe.get('type')}) "
              f"→ действие: {action}")
        self._right_click_at(bx, by)
        self._mission = 'SAFE_MENU_WAIT'
        self._mission_ts = now
        self._current_anom = f"safe:{safe.get('label')}"
        self._safe_action = action


    def _focus_maintain(self, state, off_x, off_y, now):
        """Фокус: одна захваченная цель = атакуемая цель (пушки + дроны).

        Фокус ставится на первую захваченную цель и НЕ
        меняется, пока она жива — пушки и дроны бьют одну цель. Переключение
        только после уничтожения цели в фокусе: берём следующую захваченную
        (фрегаты приоритетнее как скрам-угроза), кликом по её строке делаем
        её активной, включаем орбиту вокруг неё и посылаем дроны (F).
        """
        locked = list(state.targets or [])
        if not locked:
            self._focus_name = None
            return
        names = [_norm_name(t.get('name')) for t in locked]
        # Если фокус жив, но вышел за пределы лока — сбрасываем его,
        # чтобы _focus_maintain ниже выбрал новую цель.
        if self._focus_name:
            focus_dist = None
            for e in (getattr(state, 'overview_entries', None) or []):
                if _norm_name(e.get('name')) == _norm_name(self._focus_name):
                    focus_dist = float(e.get('distance') or 0.0)
                    break
            limit_m = None
            if getattr(state, 'lock_range_km', None):
                limit_m = float(state.lock_range_km) * 1000.0
            if limit_m and focus_dist and focus_dist > limit_m:
                print(f"[Combat] Фокус '{self._focus_name}' вышел за "
                      f"{state.lock_range_km} км — сбрасываю")
                self._focus_name = None
                # Дальше код пойдёт по обычной логике выбора фокуса.
        if self._focus_name and _norm_name(self._focus_name) in names:
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
        # Всегда кликаем по строке лока выбранной цели — гарантируем,
        # что она активна (в дампе активная цель помечена ActiveTargetIndicator).
        if not state.active_target or \
                _norm_name(state.active_target.get('name')) != _norm_name(self._focus_name):
            self._click_target_row(nxt, off_x, off_y)
            self._last_lock_action_ts = now
        # Орбита вокруг новой цели через engage (фаза 1: выделить + W)
        coords = (nxt.get('x'), nxt.get('y'))
        if coords[0] and coords[1]:
            if now - self._engage_last_ts >= self.cfg['engage_repress_cooldown']:
                self._engage_target(state, off_x, off_y,
                                    target_name=self._focus_name, coords=coords,
                                    do_lock=False)
        # Дроны атакуют новую цель фокуса
        if self.use_drones and state.drones_in_space > 0:
            self._engage_drones_on_target(state, off_x, off_y)


    def _check_warp_out_conditions(self, state) -> bool:
        """Экстренный выход из боя (HP, local). True = бой прерван."""
        if getattr(self, '_safety_active', False):
            return True
        if self._is_low_hp(state):
            self._safety_active = True
            self._safety_reason = 'low_hp'
            self.stats['escapes'] += 1
            self._last_hp_warp_ts = time.monotonic()
            print("[!!!] SAFETY: низкий HP — прерываю бой")
            self._warp_to_safe(state)
            return True
        if (self.cfg.get('local_control_enabled', True)
                and (state.local_has_hostile or state.local_has_neutral)):
            self._safety_active = True
            self._safety_reason = 'hostile'
            self.stats['escapes'] += 1
            print("[!!!] SAFETY: враг в локале — прерываю бой")
            self._warp_to_safe(state)
            return True
        return False

    def _approach_and_engage(self, state, off_x, off_y, now):
        """Сближение с ближайшей целью вне зоны лока (без лока)."""
        all_t = self._npc_distances(state)
        if not all_t:
            self._vlog_throttled('c_no_ov', "combat: овервью пуст — жду NPC")
            return
        if not self._approach_target:
            all_t.sort(key=lambda t: t[1])
            name, dist, x, y = all_t[0]
            self._approach_target = name
            self._entry_attempts = 0
            print(f"[Combat] Сближаюсь с '{name}' ({self._fmt_dist(dist)})")
        else:
            found = next((t for t in all_t
                          if t[0].lower() == self._approach_target.lower()), None)
            if not found:
                self._approach_target = None
                self._entry_attempts = 0
                return
            name, dist, x, y = found
        if state.is_orbiting and self._approach_target == name:
            self._vlog_throttled('c_approach',
                f"orbit: активна вокруг '{name}', жду сближения")
            return
        if now - self._engage_last_ts < self.cfg['entry_retry_cooldown']:
            return
        if self._entry_attempts >= self.cfg['entry_attempt_max']:
            print(f"[Combat] Сближение не удалось — баню '{name}' на 60с")
            self._skip_coords[name.lower()] = time.time()
            self._approach_target = None
            self._entry_attempts = 0
            return
        self._entry_attempts += 1
        print(f"[Combat] Попытка сближения #{self._entry_attempts} "
              f"с '{name}' ({self._fmt_dist(dist)})")
        self._engage_target(state, off_x, off_y, target_name=name,
                            coords=(x, y), do_lock=False)

    def _combat_loop(self, state, off_x, off_y, now):
        """Боевой цикл: единая точка входа для боя."""
        if self._check_warp_out_conditions(state):
            return
        if self._engage_pending:
            self._check_engage_ready(state, off_x, off_y)
            return
        if self._just_exited_warp:
            self._just_exited_warp = False
            self._combat_start_ts = now
            self._prop_keep_off_ts = 0.0
            print(f"[Combat] Выход из варпа — жду {self.cfg['warp_exit_settle']:.0f} с")
        if (self._combat_start_ts > 0 and
                now - self._combat_start_ts < self.cfg['warp_exit_settle']):
            self._vlog_throttled('c_arrive',
                f"combat: прилёт — ещё "
                f"{self.cfg['warp_exit_settle'] - (now - self._combat_start_ts):.1f} с")
            return
        if not state.targets:
            cand = self._lock_candidates(state)
            if not cand:
                self._approach_and_engage(state, off_x, off_y, now)
                return
            if now - self._engage_last_ts < self.cfg['engage_repress_cooldown']:
                return
            first = cand[0]
            print(f"[Combat] Нет локов — engage '{first['name']}' "
                  f"({self._fmt_dist(first['distance'])})")
            self._engage_target(state, off_x, off_y,
                                target_name=first['name'],
                                coords=(first['x'], first['y']),
                                do_lock=True)
            return
        if not state.is_orbiting and state.active_target:
            at = state.active_target
            coords = (at.get('x'), at.get('y'))
            if coords[0] and coords[1]:
                if now - self._engage_last_ts >= self.cfg['engage_repress_cooldown']:
                    print(f"[Combat] Орбита слетела — engage "
                          f"'{at.get('name')}' без лока")
                    self._engage_target(state, off_x, off_y,
                                        target_name=at.get('name'),
                                        coords=coords,
                                        do_lock=False)
                    return
            if not self._focus_name and state.targets:
                self._focus_name = str((state.targets[0] or {}).get('name') or '')
                self._drone_f_target = None
        if not self._focus_name or not any(
                _norm_name(t.get('name')) == _norm_name(self._focus_name)
                for t in state.targets):
            self._focus_name = str((state.targets[0] or {}).get('name') or '')
            self._drone_f_target = None
            print(f"[Combat] Фокус -> '{self._focus_name}'")
        if self.use_drones and state.drones_in_space > 0:
            self._drone_combat_cycle(state, now)
        if len(state.targets) < self.cfg['lock_batch_size']:
            self._maintain_locks(state, off_x, off_y, now)
        self._attack_until_dead(state, now)

    def _maintain_locks(self, state, off_x, off_y, now):
        """До-захват новых целей из овервью, ЖЁСТКО не больше 3 локов.

        Потолок = cfg['lock_batch_size'] (3). Если уже залокано 3+ —
        ничего не делаем. Иначе добираем до 3 с приоритетом _lock_candidates
        (фрегаты → баттлшипы → остальные, внутри — по дистанции).
        """
        max_locks = int(self.cfg.get('lock_batch_size', 3))
        locked = list(state.targets or [])
        if len(locked) >= max_locks:
            return
        need = max_locks - len(locked)
        cand = self._lock_candidates(state)
        if not cand:
            return
        cand_cnt, locked_cnt = {}, {}
        for c in cand:
            n = str(c.get('name') or '').strip().lower()
            cand_cnt[n] = cand_cnt.get(n, 0) + 1
        for t in locked:
            n = str(t.get('name') or '').strip().lower()
            locked_cnt[n] = locked_cnt.get(n, 0) + 1
        new = []
        taken_per_name = {}
        for c in cand:
            if len(new) >= need:
                break
            n = str(c.get('name') or '').strip().lower()
            already = locked_cnt.get(n, 0) + taken_per_name.get(n, 0)
            if already >= cand_cnt.get(n, 0):
                continue
            if c.get('targeted_by_me'):
                continue
            new.append(c)
            taken_per_name[n] = taken_per_name.get(n, 0) + 1
        if not new:
            return
        if now - self._last_lockup_ts < self.cfg['lockup_cooldown']:
            self._vlog_throttled('c_lockup',
                f"combat: залокано {len(locked)}/{max_locks} — до-захват "
                f"{len(new)} целей ждёт cooldown")
            return
        self._last_lockup_ts = now
        print(f"[Combat] Захвачено {len(locked)}/{max_locks} — до-захват "
              f"{len(new)} целей (Ctrl+клик)")
        for c in new:
            self._ctrl_click_at(c['x'], c['y'], off_x, off_y)
            time.sleep(self.cfg['lock_batch_delay'])

    def _click_target_row(self, target, off_x, off_y):
        """Клик по строке цели в панели лока — делает её активной (фокус огня)."""
        tx = (target.get('x') or 0) + off_x
        ty = (target.get('y') or 0) + off_y
        dist = target.get('distance') or 0.0
        print(f"[Bot] Фокус огня -> {target.get('name') or 'Target'} ({dist:.0f} m)")
        self._click_at(tx, ty)

    def _ensure_active_target(self, state, off_x, off_y):
        """Сделать активной целью фокус. True = уже активна (или фокуса нет);
        False = кликнули по строке лока (цель станет активной на след. тике).

        Анти-спам: клик по строке фокуса не чаще 1.5 сек. Дампу нужно
        1-2 тика, чтобы отразить смену ActiveTargetIndicator, и без
        анти-спама бот спамит клики вместо того, чтобы дождаться
        обновления и нажать F.
        """
        if not self._focus_name:
            return True
        fn = _norm_name(self._focus_name)
        focus = None
        for t in (state.targets or []):
            if _norm_name(t.get('name')) == fn:
                focus = t
                break
        if focus is None:
            return True   # фокуса нет среди локов — нечего переключать
        active = getattr(state, 'active_target', None)
        if focus.get('is_active') or _norm_name((active or {}).get('name')) == fn:
            return True   # уже активна (is_active=True в ряду лока)
        now = time.monotonic()
        if now - self._last_focus_click_ts < 1.5:
            # Уже кликали недавно — ждём обновления дампа, не спамим
            return False
        self._last_focus_click_ts = now
        print(f"[Combat] Делаю активной цель фокуса '{self._focus_name}' "
              f"(клик по строке лока)")
        self._click_target_row(focus, off_x, off_y)
        return False

    def _send_drones_f(self, state, now):
        """Нажать F (дроны атакуют цель фокуса).

        Отметка отправки: _drone_f_target = имя текущей цели фокуса,
        _drone_f_sent_ts — время нажатия. Вызывается:
          * при смене цели фокуса (старая уничтожена);
          * повторно ПОКА state.drones_fighting=False — по таймауту
            drone_f_repress_cooldown (см. _drone_combat_cycle, FIGHTING).
        Постоянного спама F нет: не чаще 1 раза в 6 сек и только когда
        цель фокуса АКТИВНА в UI (проверено _ensure_active_target).
        """
        print(f"[Bot] Дроны -> захваченная цель '{self._focus_name}' (F)")
        self._key_down_up('f')
        self._drone_f_target = self._focus_name
        self._drone_f_sent_ts = now
        self._last_drone_press = now
        time.sleep(1)

    def _engage_drones_on_target(self, state, off_x, off_y):
        """Послать дронов на цель фокуса. Жмёт F (VK-код) только если активная
        цель в UI = фокус; иначе сначала кликает по строке фокуса и вернётся.
        True = F отправлен, False = нужен ещё тик на переключение цели."""
        if not self._focus_name:
            return False
        if state.drones_in_bay > 0 and state.drones_in_space == 0:
            # Дроны в ангаре: F бесполезен — сначала выпуск (Shift+F),
            # F откладываем на след. тик.
            print("[Bot] Дроны в ангаре — сначала выпуск (Shift+F), F отложен")
            self._press_shift('f')
            time.sleep(5)
            return False
        focus = None
        fn = _norm_name(self._focus_name)
        for t in (state.targets or []):
            if _norm_name(t.get('name')) == fn:
                focus = t
                break
        if focus is None:
            self._vlog_throttled('d_nofocus',
                f"drones: цель фокуса '{self._focus_name}' не в локах — F не жму")
            return False
        active = getattr(state, 'active_target', None)
        # F в EVE уходит дронам только на АКТИВНУЮ цель: если захваченный NPC
        # не активен (is_active=False) — кликаем по его строке в ряду лока
        # и ждём след. тик, пока не активируется (is_active=True).
        if not (focus.get('is_active') or
                _norm_name((active or {}).get('name')) == fn):
            print(f"[Combat] Делаю активной цель фокуса '{self._focus_name}' "
                  f"(клик по строке лока)")
            self._click_target_row(focus, off_x, off_y)
            return False
        # F — ровно один раз на цель фокуса: если F уже отправлялся для
        # ЭТОЙ цели — не дублируем (повторы по таймауту делает
        # _drone_combat_cycle/FIGHTING, а здесь только первый F).
        now = time.monotonic()
        if self._drone_f_target == self._focus_name:
            return True   # F уже отправлен для этой цели
        self._send_drones_f(state, now)
        return True

    def _click_at(self, x, y):
        """Клик по абсолютным координатам окна с рандомизацией (анти-детерминизм)."""
        # Смещение вправо от края иконки + разброс по Y и скорости
        target_x = x + random.randint(5, 30)
        target_y = y + random.randint(-2, 2)
        pyautogui.moveTo(target_x, target_y, duration=0.3)
        time.sleep(0.3)
        pyautogui.click()
        time.sleep(0.3)

        # в) Дроны обрабатываются в _drone_combat_cycle (см. выше)

    def _ctrl_click_at(self, lx, ly, off_x, off_y):
        """Захват цели: Ctrl + LMB клик по строке овервью.

        Ctrl зажимается ДО клика с паузой ctrl_click_pause — иначе EVE
        не успевает зарегистрировать модификатор и клик проходит как
        обычный (цель выделяется, но не захватывается в лок).

        Клик делается НАПРЯМУЮ (moveTo + click), а не через _click_at:
        внутри _click_at свои moveTo/sleep/рандом, из-за которых Ctrl
        успевает отпуститься раньше, чем EVE обработает клик —
        захват не срабатывает. Здесь: курсор поставили, Ctrl зажали,
        сразу кликнули, пауза на регистрацию, Ctrl отпустили.
        """
        pyautogui.moveTo(lx + off_x, ly + off_y, duration=0.3)
        time.sleep(0.3)
        with pyautogui.hold('ctrl'):
            time.sleep(0.3)
            pyautogui.click()
            time.sleep(0.3)

    # ==================== ДРОНЫ: ЦИКЛ ВЫЖИВАНИЯ ====================

    def _drone_combat_cycle(self, state, now):
        """Микро-FSM: FIGHTING -> RECALLING -> WAITING_ATTACK -> COOLDOWN -> FIGHTING.

        Контроль состояния дронов:
        1. Если HP дронов ниже 50% (полоса урона > половины) -> собираем
           (Shift+R). Пушки продолжают стрелять.
        2. После сбора ждём, пока >=80% враждебных NPC в овервью не начнут
           атаковать КОРАБЛЬ (attacking_me) — но не дольше drone_release_wait.
        3. Когда все дроны в ангаре (status in bay) -> пауза ->
           перезапуск (Shift+F) -> атака (F).
        4. Если дронов нет в ангаре и в космосе — алерт (дроны потеряны).
        """
        if self._drone_phase == 'FIGHTING':
            # 1. Низкий HP дронов (<50%) по полосам HP.
            #    droneGaugeBar — это ОСТАТОК HP (сжимается при уроне);
            #    droneGaugeBarDmg — снятый урон (растёт).
            #    Полная полоса = остаток + снятый урон; доля урона = dmg / (bar + dmg).
            #    Собираем дронов только когда урон превысил половину полной полосы.
            low_hp = []
            for d in state.drone_entries or []:
                bar_w = d.get('bar_width') or 0
                dmg_w = d.get('damage_width') or 0
                full_w = bar_w + dmg_w          # остаток + снятый урон = полная полоса
                if full_w > 0 and (dmg_w / full_w) > 0.5:
                    low_hp.append(d)
            if low_hp and state.drones_in_space > 0:
                names = ', '.join(str(d.get('name') or '?') for d in low_hp)
                print(f"[Bot][Drones] Низкий HP (<50%): {names} — собираю (Shift+R)")
                self._press_shift('r')
                self._drone_phase = 'RECALLING'
                return

            # 2. Дроны в космосе + есть захваченная цель в UI.
            #    F жмётся при смене цели фокуса; повтор — по таймауту
            #    drone_f_repress_cooldown, ПОКА дроны не атакуют
            #    (state.drones_fighting=False). Агрегатный
            #    state.drones_fighting НЕ блокирует F на новую цель
            #    фокуса: он True, если ХОТЯ БЫ ОДИН дрон в статусе
            #    FIGHTING, в том числе по СТАРОЙ цели — раньше из-за
            #    этого бот пропускал F на новую цель.
            if state.drones_in_space > 0 and (state.targets or []):
                if not self._focus_name:
                    self._focus_name = str((state.targets[0] or {}).get('name') or '')
                off_x, off_y = self._get_offset()
                if self._ensure_active_target(state, off_x, off_y):
                    if self._drone_f_target != self._focus_name:
                        # Новая цель фокуса — F ни разу не отправлялся
                        self._send_drones_f(state, now)
                    elif not state.drones_fighting:
                        # Дроны НЕ атакуют — страховка: цель активна,
                        # повтор F не чаще drone_f_repress_cooldown
                        if (now - self._drone_f_sent_ts >=
                                self.cfg['drone_f_repress_cooldown']):
                            print(f"[Bot] Дроны не атакуют "
                                  f"'{self._focus_name}' — повтор F")
                            self._send_drones_f(state, now)
                    # F отправлен, дроны атакуют — не мешаем
                return

            # 3. Выпуск дронов, если в ангаре.
            if state.drones_in_bay > 0 and state.drones_in_space == 0:
                print("[Bot] Выпускаю дронов (Shift+F)")
                self._press_shift('f')
                time.sleep(5)  # Ждем, пока дроны выпустятся

        elif self._drone_phase == 'RECALLING':
            if state.drones_in_space == 0:
                self._drone_phase = 'WAITING_ATTACK'
                self._drone_wait_start_ts = now
                print("[Bot][Drones] Дроны в ангаре. Жду attackingMe ≥80% для выпуска...")

        elif self._drone_phase == 'WAITING_ATTACK':
            # Считаем долю NPC, которые атакуют КОРАБЛЬ
            entries = getattr(state, 'overview_entries', None) or []
            hostile = [e for e in entries
                       if e.get('hostile') or e.get('attacking_me') or e.get('threat_hostile')]
            total = len(hostile)
            attacking_me = sum(1 for e in hostile if e.get('attacking_me'))
            ratio = (attacking_me / total) if total > 0 else 0.0

            elapsed = now - self._drone_wait_start_ts
            release = (ratio >= 0.8) or (elapsed >= self.cfg['drone_release_wait'])

            if release:
                if ratio >= 0.8:
                    why = f"{attacking_me}/{total} attackingMe"
                else:
                    why = f"timeout {elapsed:.0f}s"
                print(f"[Bot][Drones] Условие выпуска: {why} — перехожу в COOLDOWN")
                self._drone_phase = 'COOLDOWN'
                self._drone_cooldown_until = now + self.cfg['drone_relaunch_delay']
            else:
                self._vlog_throttled('d_wait',
                    f"drones: жду attackingMe ≥80% (сейчас {attacking_me}/{total}, "
                    f"ещё {max(0, self.cfg['drone_release_wait'] - elapsed):.0f}с)")

        elif self._drone_phase == 'COOLDOWN':
            if now >= self._drone_cooldown_until:
                off_x, off_y = self._get_offset()
                if self._ensure_active_target(state, off_x, off_y):
                    print("[Bot][Drones] Перезапускаю дронов в бой (Shift+F)")
                    self._press_shift('f')
                    time.sleep(5)
                    self._drone_phase = 'FIGHTING'

    def _press_shift(self, key):
        """Shift+<key> (выпуск/сбор дронов)."""
        pyautogui.keyDown('shift')          # модификатор — pyautogui (ок)
        time.sleep(0.3)  # Даем игре время понять, что Shift зажат
        self._key_down_up(key)              # VK-код: работает на RU/EN
        time.sleep(0.3)
        pyautogui.keyUp('shift')

    # ==================== СТАТИСТИКА СЕССИИ ====================

    def _track_kills(self, state):
        """Киллы: число захваченных целей уменьшилось, пока мы на сайте."""
        cur = len(state.targets or [])
        has_npcs = bool(state.npc_all or (getattr(state, 'overview_entries', None) or []))
        if cur < self._prev_locked_count and has_npcs:
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
        has_npcs = bool(state.npc_all or (getattr(state, 'overview_entries', None) or []))
        if state.drones_in_space > 0 and not has_npcs:
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
                         duration=0.3)
        time.sleep(0.3)
        pyautogui.click(button='right')
        time.sleep(0.3)

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

        # --- безопасность: отварп на сейф-букмарк ---
        if self._mission == 'SAFE_MENU_WAIT':
            now_t = time.monotonic()
            if state.menu_open and state.menu_items:
                if now_t - self._mission_ts < self.cfg['menu_settle']:
                    return
                item = None
                action = getattr(self, '_safe_action', 'warp')
                entries = [it for it in (state.menu_items or [])
                           if it.get('type') == 'entry']

                if action == 'dock':
                    # Для станции ищем 'Dock'
                    for it in entries:
                        txt = str(it.get('text') or '').strip().lower()
                        if txt == 'dock' or txt.startswith('dock'):
                            item = it
                            break
                    # Фолбэк: если Dock нет (станция не наша) — Warp to Within 0 m
                    if item is None:
                        for it in entries:
                            txt = str(it.get('text') or '').lower()
                            if 'warp to within 0 m' in txt:
                                item = it
                                print("[Safety] Пункт 'Dock' не найден — фолбэк на Warp")
                                break

                if item is None:
                    # Coordinate или фолбэк: 'Warp to Within 0 m' / 'Warp to Location'
                    for it in entries:
                        txt = str(it.get('text') or '').lower()
                        if 'warp to within 0 m' in txt:
                            item = it
                            break
                        if 'warp to location' in txt:
                            item = it
                            break

                if item:
                    off_x, off_y = self._get_offset()
                    print(f"[Safety] Пункт '{item.get('text')}' — клик")
                    self._click_at((item.get('x') or 0) + (item.get('w') or 160) // 2 + off_x,
                                   (item.get('y') or 0) + (item.get('h') or 22) // 2 + off_y)
                    self._reset_mission()
                else:
                    print("[Safety] Нужный пункт в меню не найден — сбрасываю миссию")
                    self._reset_mission()
            elif now_t - self._mission_ts >= self.cfg['menu_open_timeout']:
                print("[Safety] Меню не открылось — повтор ПКМ")
                self._warp_to_safe(state)
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
                        print("[Mission] Пункт Warp в меню не найден — закрываю меню ЛКМ")
                        w = state.window_width or 1445
                        h = state.window_height or 1001
                        off_x2, off_y2 = self._get_offset()
                        pyautogui.click(off_x2 + int(w * 0.55), off_y2 + int(h * 0.30))
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

    def apply_action(self, action: dict) -> bool:
        """Применить решение нейросети (или заглушки nn_server).

        Возвращает True, если действие распознано и выполнено (или поставлено
        в очередь исполнения). Сейчас реализованы только EMERGENCY_EXIT и IDLE:
        минимальный набор, чтобы канал NN -> bot был проверен.
        Остальные действия логируем и игнорируем — их реализуем позже,
        когда появится нейросеть.

        Ожидаемые action:
          EMERGENCY_EXIT    — выйти в безопасность (уже реализовано)
          IDLE              — ничего не делать, FSM сама работает
          ENGAGE_TARGETS    — (позже) атаковать цели из params.lock_candidates
          RECALL_DRONES     — (позже) собрать дронов
          ACTIVATE_REPAIRER — (позже) включить указанный слот репки
        """
        if not isinstance(action, dict):
            return False
        a = str(action.get('action') or 'IDLE')

        if a == 'EMERGENCY_EXIT':
            reason = action.get('reason') or 'NN decision'
            self.emergency_exit(reason)
            return True

        if a == 'IDLE':
            return True

        # Остальные команды пока не реализованы — лог для отладки канала.
        print(f"[Bot][NN] action={a} params={action.get('params')} — not implemented yet")
        return False