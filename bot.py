# bot.py
"""
EVE Online combat bot — переписан с нуля.

Архитектура:
  EveBot              — тонкий фасад (think / set_window / apply_action / request_stop)
   ├─ WindowManager   — поиск окна EVE по exefile.exe, offset, активация
   ├─ InputController — весь ввод: клавиши (scancode), клики (ЛКМ/ПКМ/Ctrl/W)
   ├─ SafetyController— HP/local → отход на сейф-букмарк
   ├─ MissionFSM      — полёт по аномалиям из probe scanner
   ├─ CombatFSM       — единый боевой автомат (SELECT→ORBIT→LOCK→ATTACK)
   ├─ DroneController — управление дронами (выпуск / F / recall / relaunch)
   └─ ModuleController— клики по HUD (репки, двигатель) с подтверждением из дампа

Внешний API сохранён с предыдущей версии, чтобы main.py работал без правок:
  EveBot(stats=...), set_window(), list_eve_windows(), think(), request_stop(),
  apply_action(), verbose, cfg, _mission, _current_anom, reset_session_state().
"""

import ctypes
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import pyautogui
import pygetwindow as gw

# =============================================================================
# WIN32 / SCANCODE INPUT
# =============================================================================
# keybd_event(VK) на RU-раскладке отправляет РУССКУЮ букву (W → Ц). EVE
# матчит физический скан-код, поэтому используем SendInput + KEYEVENTF_SCANCODE.
# Это единственный способ надёжно слать 'W' для хоткея Orbit (W + ЛКМ).

try:
    _USER32 = ctypes.windll.user32
except Exception:
    _USER32 = None

try:
    _KERNEL32 = ctypes.windll.kernel32
except Exception:
    _KERNEL32 = None

_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_SCANCODE = 0x0008
_INPUT_KEYBOARD = 1

_SCANCODES = {
    'w': 0x11, 'a': 0x1E, 's': 0x1F, 'd': 0x20, 'q': 0x10,
    'e': 0x12, 'r': 0x13, 'f': 0x21, 'g': 0x22,
    'enter': 0x1C, 'return': 0x1C, 'space': 0x39, 'esc': 0x01,
    'shift': 0x2A, 'ctrl': 0x1D, 'alt': 0x38,
    'f1': 0x3B, 'f2': 0x3C, 'f3': 0x3D, 'f4': 0x3E,
    'f5': 0x3F, 'f6': 0x40, 'f7': 0x41, 'f8': 0x42,
}

_VK_NAMED = {
    'enter': 0x0D, 'return': 0x0D, 'esc': 0x1B, 'escape': 0x1B,
    'tab': 0x09, 'space': 0x20, 'backspace': 0x08,
    'shift': 0x10, 'ctrl': 0x11, 'control': 0x11, 'alt': 0x12,
    'f1': 0x70, 'f2': 0x71, 'f3': 0x72, 'f4': 0x73, 'f5': 0x74,
    'f6': 0x75, 'f7': 0x76, 'f8': 0x77,
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


def _send_scancode(scancode: int, up: bool = False) -> bool:
    """Отправить физический скан-код (не зависит от раскладки)."""
    if _USER32 is None:
        return False
    flags = _KEYEVENTF_SCANCODE | (_KEYEVENTF_KEYUP if up else 0)
    inp = _INPUT(
        type=_INPUT_KEYBOARD,
        ki=_KEYBDINPUT(wVk=0, wScan=scancode, dwFlags=flags, time=0,
                       dwExtraInfo=None),
    )
    try:
        return _USER32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)) == 1
    except Exception:
        return False


def _vk_for(key: str) -> Optional[int]:
    k = str(key).lower()
    if len(k) == 1:
        if 'a' <= k <= 'z':
            return ord(k.upper())
        if '0' <= k <= '9':
            return ord(k)
    return _VK_NAMED.get(k)


def _win_key_event(key: str, up: bool = False) -> bool:
    vk = _vk_for(key)
    if vk is None or _USER32 is None:
        return False
    try:
        _USER32.keybd_event(vk, 0, _KEYEVENTF_KEYUP if up else 0, 0)
        return True
    except Exception:
        return False


# =============================================================================
# НАТИВНАЯ МЫШЬ (Win32) — заменяет pyautogui для кликов и moveTo
# =============================================================================
# pyautogui.moveTo / click на Windows может блокироваться: смена фокуса,
# второй монитор, RDP, антивирус, sluggish message pump целевого окна.
# SetCursorPos и SendInput(MOUSEEVENTF_*) работают напрямую через user32
# и НЕ зависают. Это критично: если think() зависнет, поток _bot_thread
# останется is_alive()=True навсегда, и _start_bot_thread перестанет
# запускать новые тики.

_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010


def _native_move_to(x: int, y: int) -> bool:
    """Поставить курсор в (x, y) через SetCursorPos. True — успех."""
    if _USER32 is None:
        return False
    try:
        return bool(_USER32.SetCursorPos(int(x), int(y)))
    except Exception:
        return False


def _native_mouse_event(flag: int) -> bool:
    """Отправить mouse_event с флагом (LEFTDOWN/LEFTUP/RIGHTDOWN/RIGHTUP)."""
    if _USER32 is None:
        return False
    try:
        _USER32.mouse_event(flag, 0, 0, 0, 0)
        return True
    except Exception:
        return False


def _native_left_click() -> bool:
    """ЛКМ: LEFTDOWN + пауза + LEFTUP через mouse_event."""
    if _USER32 is None:
        return False
    try:
        _USER32.mouse_event(_MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.03)
        _USER32.mouse_event(_MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        return True
    except Exception:
        return False


def _native_right_click() -> bool:
    """ПКМ: RIGHTDOWN + пауза + RIGHTUP через mouse_event."""
    if _USER32 is None:
        return False
    try:
        _USER32.mouse_event(_MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
        time.sleep(0.03)
        _USER32.mouse_event(_MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
        return True
    except Exception:
        return False


def norm_name(s: Any) -> str:
    """Нормализация имени: схлопнуть пробелы + casefold."""
    return ' '.join(str(s or '').split()).casefold()


# =============================================================================
# INPUT CONTROLLER
# =============================================================================

class InputController:
    """Весь ввод в одном месте.

    Отвечает за:
      * клики (ЛКМ/ПКМ) с рандомизацией,
      * Ctrl+клик (захват цели), W+клик (орбита) — с корректным удержанием
        модификатора ДО клика (иначе EVE не видит модификатор),
      * клавиши: одиночные, scancode-нажатия, Shift+key, Ctrl+key,
      * анти-даблпресс (повторная клавиша выключает модуль — режем по кулдауну).
    """

    def __init__(self, cfg: Dict[str, Any], verbose_getter=None):
        self.cfg = cfg
        self._verbose = verbose_getter or (lambda: False)
        self._recent_keys: Dict[str, float] = {}
        pyautogui.PAUSE = 0.02   # только защита от случайных даблкликов

    # ---------- клики ----------
    #
    # Все клики — через нативные Win32-хелперы (_native_move_to,
    # _native_left_click, _native_right_click). pyautogui.moveTo и
    # pyautogui.click на Windows могут блокироваться, оставляя think()
    # висеть и полностью останавливая бот (см. диагностику).
    # Если нативный вызов недоступен (не Windows / user32 не загрузился),
    # падаем обратно на pyautogui.

    def _move(self, x: int, y: int) -> None:
        """Курсор в (x, y). Нативный SetCursorPos, фолбэк — pyautogui."""
        if not _native_move_to(int(x), int(y)):
            try:
                pyautogui.moveTo(int(x), int(y), duration=0.05)
            except Exception:
                pass

    def _left_click(self) -> None:
        """ЛКМ. Нативный mouse_event, фолбэк — pyautogui.click."""
        if not _native_left_click():
            try:
                pyautogui.click()
            except Exception:
                pass

    def _right_click(self) -> None:
        """ПКМ. Нативный mouse_event, фолбэк — pyautogui.click(button='right')."""
        if not _native_right_click():
            try:
                pyautogui.click(button='right')
            except Exception:
                pass

    def click(self, x: int, y: int, jitter: bool = True,
              duration: float = 0.25) -> None:
        if jitter:
            x += random.randint(4, 18)
            y += random.randint(-2, 2)
        self._move(x, y)
        time.sleep(0.05)
        self._left_click()
        time.sleep(0.05)

    def click_raw(self, x: int, y: int, duration: float = 0.2) -> None:
        """Клик без рандомизации — для точных попаданий (строки меню, кнопки)."""
        self._move(x, y)
        time.sleep(0.05)
        self._left_click()
        time.sleep(0.05)

    def right_click(self, x: int, y: int, jitter: bool = True) -> None:
        if jitter:
            x += random.randint(3, 15)
            y += random.randint(-2, 2)
        self._move(x, y)
        time.sleep(0.05)
        self._right_click()
        time.sleep(0.05)

    def ctrl_click(self, x: int, y: int, duration: float = 0.2) -> None:
        """Ctrl зажат ДО клика — иначе EVE не видит модификатор."""
        self._move(x, y)
        time.sleep(0.1)
        pyautogui.keyDown('ctrl')
        try:
            time.sleep(0.1)
            self._left_click()
            time.sleep(0.1)
        finally:
            pyautogui.keyUp('ctrl')

    def w_click(self, x: int, y: int, hold_after: float = 0.05,
                duration: float = 0.15) -> None:
        """W зажат физически (scancode), клик, отпустить. Для Orbit."""
        self._physical_down('w')
        try:
            self._move(x, y)
            time.sleep(0.05)
            self._left_click()
            time.sleep(hold_after)
        finally:
            self._physical_up('w')

    def w_down(self) -> None:
        """Зажать W физически (для последовательности SELECT → ORBIT_WAIT)."""
        self._physical_down('w')

    def w_up(self) -> None:
        """Отпустить W физически."""
        self._physical_up('w')

    # ---------- клавиши ----------

    # ---------- клавиши ----------

    def press(self, key: str, cooldown: Optional[float] = None) -> bool:
        """Одиночное нажатие с анти-даблпрессом. False — съедено кулдауном."""
        now = time.monotonic()
        cd = self.cfg.get('key_repress_cooldown', 6.0) if cooldown is None else cooldown
        last = self._recent_keys.get(key, 0.0)
        if now - last < cd:
            return False
        self._recent_keys[key] = now
        pyautogui.press(key)
        return True

    def press_physical(self, key: str) -> None:
        """Нажатие через scancode (для 'w'/'f' и пр. на RU-раскладке)."""
        self._physical_down(key)
        time.sleep(0.05)
        self._physical_up(key)

    def shift_press(self, key: str) -> None:
        """Shift + key. Shift — через pyautogui (модификатор), key — scancode."""
        pyautogui.keyDown('shift')
        time.sleep(0.25)
        self._physical_down(key)
        time.sleep(0.05)
        self._physical_up(key)
        time.sleep(0.2)
        pyautogui.keyUp('shift')

    def ctrl_press(self, key: str) -> None:
        """Ctrl + key (для alt+ctrl-хоткеев EVE)."""
        pyautogui.keyDown('ctrl')
        time.sleep(0.15)
        pyautogui.press(key)
        time.sleep(0.15)
        pyautogui.keyUp('ctrl')

    def alt_press(self, key: str) -> None:
        pyautogui.keyDown('alt')
        time.sleep(0.1)
        pyautogui.press(key)
        time.sleep(0.1)
        pyautogui.keyUp('alt')

    def alt_hold_press(self, key: str) -> None:
        """Alt зажат, key нажат через scancode (Alt+F1 активирует модуль)."""
        pyautogui.keyDown('alt')
        time.sleep(0.1)
        self._physical_down(key)
        time.sleep(0.05)
        self._physical_up(key)
        time.sleep(0.1)
        pyautogui.keyUp('alt')

    # ---------- внутреннее ----------

    def _physical_down(self, key: str) -> None:
        k = str(key).lower()
        sc = _SCANCODES.get(k)
        if sc is not None and _send_scancode(sc, up=False):
            return
        _win_key_event(k, up=False)

    def _physical_up(self, key: str) -> None:
        k = str(key).lower()
        sc = _SCANCODES.get(k)
        if sc is not None and _send_scancode(sc, up=True):
            return
        _win_key_event(k, up=True)

# =============================================================================
# WINDOW MANAGER
# =============================================================================

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_GW_OWNER = 4


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


_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def _list_exefile_pids() -> set:
    if _KERNEL32 is None:
        return set()
    pids = set()
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
    if _USER32 is None:
        return []
    result = []

    def _cb(hwnd, _l):
        wnd_pid = ctypes.c_ulong(0)
        _USER32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(wnd_pid))
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
    if _USER32 is None:
        return ''
    buf = ctypes.create_unicode_buffer(512)
    _USER32.GetWindowTextW(ctypes.c_void_p(hwnd), buf, 512)
    return buf.value


def _get_window_rect(hwnd: int) -> Tuple[int, int, int, int]:
    if _USER32 is None:
        return (0, 0, 0, 0)

    class _RECT(ctypes.Structure):
        _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                    ('right', ctypes.c_long), ('bottom', ctypes.c_long)]

    r = _RECT()
    if not _USER32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r)):
        return (0, 0, 0, 0)
    return (int(r.left), int(r.top), int(r.right), int(r.bottom))


_EVE_WINDOW_TITLE_RE = re.compile(
    r"^EVE\s+-\s+"
    r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_'\-]*"
    r"(?:\s+[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_'\-]*){0,2}"
    r"$"
)

_EVE_WINDOW_TITLE_BLACKLIST = {
    'eve online', 'launcher', 'client', 'eve launcher',
}


class WindowManager:
    """Поиск HWND окна EVE, offset кликов, активация."""

    CORRECTION_Y = 31

    def __init__(self, verbose_getter=None):
        self.window_name: Optional[str] = None
        self.window_hwnd: Optional[int] = None
        self._verbose = verbose_getter or (lambda: False)

    # ---------- публичный API ----------

    def set_window(self, title: str) -> None:
        self.window_name = str(title)
        self.window_hwnd = self._find_hwnd_by_title(self.window_name)
        if self.window_hwnd:
            print(f"[Bot] Выбрано окно EVE: {self.window_name} "
                  f"(hwnd=0x{self.window_hwnd:X})")
        else:
            print(f"[Bot] Выбрано окно EVE: {self.window_name} "
                  f"(HWND не найден — fallback pygetwindow)")

    def list_eve_windows(self) -> list:
        result = []
        if _USER32 is None or _KERNEL32 is None:
            return result
        pids = _list_exefile_pids()
        if not pids:
            return result

        for pid in pids:
            candidates = []
            for hwnd in _enum_windows_for_pid(pid):
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
                    'left': left, 'top': top,
                    'width': width, 'height': height,
                    'is_active': bool(_USER32.GetForegroundWindow() == hwnd),
                    'pid': pid,
                })
            if not candidates:
                continue
            best = max(candidates, key=lambda w: w['width'] * w['height'])
            result.append(best)

        result.sort(key=lambda x: x['title'])
        return result

    def offset(self) -> Tuple[int, int]:
        """Экранные координаты окна (left, top + CORRECTION_Y)."""
        if self.window_hwnd:
            try:
                left, top, r, b = _get_window_rect(self.window_hwnd)
                if left or top or r or b:
                    return int(left), int(top) + self.CORRECTION_Y
            except Exception as e:
                print(f"[Bot] offset: GetWindowRect failed: {e}")
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

    def activate(self) -> bool:
        if self.window_hwnd:
            try:
                if _USER32.SetForegroundWindow(ctypes.c_void_p(self.window_hwnd)):
                    return True
            except Exception as e:
                print(f"[Bot] SetForegroundWindow failed: {e}")
        if not self.window_name:
            return False
        try:
            windows = gw.getWindowsWithTitle(self.window_name)
        except Exception as e:
            print(f"[Bot] activate: {e}")
            return False
        if not windows:
            return False
        win = windows[0]
        if not win.isActive:
            try:
                win.activate()
                time.sleep(0.3)
            except Exception as e:
                print(f"[Bot] activate: {e}")
                return False
        return True

    # ---------- внутреннее ----------

    def _find_hwnd_by_title(self, title: str) -> Optional[int]:
        if _USER32 is None or _KERNEL32 is None:
            return None
        best_hwnd = None
        best_area = -1
        for pid in _list_exefile_pids():
            for hwnd in _enum_windows_for_pid(pid):
                if _get_window_title(hwnd) != title:
                    continue
                left, top, right, bottom = _get_window_rect(hwnd)
                area = max(0, right - left) * max(0, bottom - top)
                if area > best_area:
                    best_area = area
                    best_hwnd = hwnd
        return best_hwnd


# =============================================================================
# MENU CONTROLLER — общий для MissionFSM и SafetyController
# =============================================================================

class MenuController:
    """Выбор пунктов в контекстном меню EVE.

    Используется:
      * MissionFSM — 'Warp to Within' + подменю 'within N km';
      * SafetyController — 'Dock' (станция) или 'Warp to Within 0 m' (Coordinate).
    """

    def __init__(self, input_ctrl: InputController, cfg: Dict[str, Any]):
        self.input = input_ctrl
        self.cfg = cfg

    def find_entry(self, items: List[Dict], predicate) -> Optional[Dict]:
        for it in items or []:
            if it.get('type') != 'entry':
                continue
            if predicate(it):
                return it
        return None

    def pick_warp_item(self, items: List[Dict]) -> Optional[Dict]:
        """Выбор warp-пункта в меню аномалии.

        Приоритет:
          1) 'Warp to Within' БЕЗ цифры в названии (подменю дистанций);
          2) любой warp с has_submenu;
          3) 'Warp to Within N km' = конфиг;
          4) любой warp, кроме 'Warp to Within 0 m'.
        """
        want = self.cfg.get('warp_distance_km', 70)

        def _has_digit(s: str) -> bool:
            return bool(re.search(r'\d', s))

        # 1) 'Warp to Within' без цифры
        for it in items or []:
            if it.get('type') != 'entry':
                continue
            txt = str(it.get('text') or '').strip().lower()
            if txt.startswith('warp to within'):
                rest = txt[len('warp to within'):].strip()
                if not rest or _has_digit(rest):
                    continue
                return it
        # 2) любой warp с подменю
        for it in items or []:
            if it.get('type') != 'entry':
                continue
            txt = str(it.get('text') or '').lower()
            if 'warp' in txt and it.get('has_submenu'):
                return it
        # 3) 'Warp to Within {want} km'
        for it in items or []:
            if it.get('type') != 'entry':
                continue
            txt = str(it.get('text') or '').lower()
            if 'warp' in txt and f'within {want} km' in txt:
                return it
        # 4) любой warp, кроме 0m и без скобок
        for it in items or []:
            if it.get('type') != 'entry':
                continue
            txt = str(it.get('text') or '').lower()
            if 'warp' in txt and '0 m' not in txt and '(' not in txt:
                return it
        return None

    def pick_distance_item(self, items: List[Dict]) -> Optional[Dict]:
        """Подменю дистанций: 'within N km' = конфиг, иначе первая НЕ 0."""
        want = self.cfg.get('warp_distance_km', 70)
        target = None
        fallback = None
        for it in items or []:
            if it.get('type') != 'entry':
                continue
            txt = str(it.get('text') or '').lower()
            if 'within' not in txt:
                continue
            if f'within {want} km' in txt:
                target = it
                break
            if 'km' in txt and '0 m' not in txt and fallback is None:
                fallback = it
        return target or fallback

    def pick_dock_or_warp0(self, items: List[Dict],
                           prefer_dock: bool) -> Optional[Dict]:
        """Для Safety: 'Dock' если prefer_dock, иначе 'Warp to Within 0 m'."""
        if prefer_dock:
            dock = self.find_entry(
                items, lambda it: str(it.get('text') or '').strip().lower().startswith('dock'))
            if dock:
                return dock
        warp0 = self.find_entry(
            items, lambda it: 'warp to within 0 m' in str(it.get('text') or '').lower())
        if warp0:
            return warp0
        return self.find_entry(
            items, lambda it: 'warp to location' in str(it.get('text') or '').lower())

    def click_item(self, item: Dict, off_x: int, off_y: int) -> None:
        """Клик в центр строки меню."""
        x = int((item.get('x') or 0) + (item.get('w') or 160) // 2 + off_x)
        y = int((item.get('y') or 0) + (item.get('h') or 22) // 2 + off_y)
        self.input.click_raw(x, y)


# =============================================================================
# DEFAULT CFG (единый источник дефолтов; main.py перезаписывает часть ключей)
# =============================================================================

DEFAULT_CFG: Dict[str, Any] = {
    # --- безопасность: HP ---
    'hp_warp_enabled': True,
    'hp_warp_source': 'shield',          # 'shield' | 'armor' | 'min'
    'hp_warp_threshold': 40.0,
    'hp_warp_cooldown': 30.0,
    # --- безопасность: local ---
    'local_control_enabled': True,
    'safe_warp_cooldown': 5.0,
    'safe_bookmark_dock_hint': 'dock',
    'safe_bookmark_name_hint': 'safe',
    'safe_bookmark_system_only': True,
    # --- лимит лока ---
    'default_lock_range_km': 70,

    # --- бой: FSM цели ---
    'focus_switch_cooldown': 2.0,
    'select_wait': 1.0,
    'orbit_hint_timeout': 2.0,
    'orbit_w_hold_after_hint': 0.05,
    'lock_timeout': 12.0,
    'lock_max_attempts': 3,
    'entry_attempt_max': 3,
    'entry_retry_cooldown': 4.0,
    'engage_repress_cooldown': 5.0,
    'lock_batch_size': 3,
    'lock_batch_delay': 0.5,
    'ctrl_click_pause': 0.3,
    'lockup_cooldown': 6.0,
    'post_frigate_priority': 'battleship',   # 'battleship' | 'random'
    # --- подход к цели вне зоны лока ---
    # Как только цель в зоне лока (distance <= lock_range * ratio) — пробуем лок.
    'approach_lock_range_ratio': 0.9,
    # Максимум ожидания сближения, сек. Не успели — бан цели.
    'approach_timeout': 30.0,
    # Проверять каждые N сек (не спамить тяжёлыми проверками).
    'approach_check_interval': 2.0,

    # --- бой: атака ---
    'auto_attack': True,
    'attack_cooldown': 4.0,
    'key_repress_cooldown': 6.0,
    'module_click_cooldown': 3.0,
    'slot_hotkey_offset': 0,
    # --- дроны ---
    'drone_relaunch_delay': 3.0,
    # Порог щита дрона для recall: 0.5 = 50%. Смотрим ТОЛЬКО щит.
    'drone_hp_recall_ratio': 0.5,
    # Первые N секунд после Shift+F дрон не считается «под атакой»:
    # пока летит к цели, щит мог быть неполным ещё до выпуска.
    'drone_hp_grace_sec': 8.0,
    'drone_f_repress_cooldown': 6.0,
    'drone_release_wait': 8.0,
    'min_drones': 1,
    'drone_hp_check_interval': 2.0,
    # --- миссия: варп к аномалиям ---
    'anomaly_group_filter': 'combat',
    'anomaly_revisit_cooldown': 600,
    'menu_open_timeout': 5.0,
    'menu_settle': 0.8,
    'submenu_settle': 1.0,
    'warp_confirm_timeout': 15.0,
    'warp_max_attempts': 3,
    'warp_distance_km': 70,
    'warp_exit_settle': 2.5,
    'arrival_settle_time': 15.0,
    # --- прилёт ---
    'modal_enter_attempts': 2,
    # --- орбита: контроль ---
    'orbit_recheck_cooldown': 6.0,
    # --- двигатель: окно «не включать обратно» после выключения ---
    'prop_keep_off_time': 25.0,
    # --- андок / probe scanner ---
    'undock_cooldown': 5.0,
    'undock_timeout': 45.0,
    'undock_settle_time': 5.0,
    'probe_open_timeout': 5.0,
    'probe_max_attempts': 3,


    # --- прочее ---
    'repair_missing_warn_interval': 60.0,
    'debug_overview': False,
    'verbose_log': False,
    # Watchdog для потока бота: если think() живёт дольше этого —
    # main.py считает поток зависшим и разрешает новый тик.
    'bot_thread_stuck_timeout': 30.0,
}

# =============================================================================
# DRONE CONTROLLER
# =============================================================================

class DroneController:
    """Управление дронами. НЕ имеет своего FSM — вызывается из CombatFSM.

    Решения:
      * дроны в ангаре + есть фокус → Shift+F (выпуск);
      * дроны в космосе + фокус сменился → F (новая цель);
      * drones_under_attack (полоса урона > 50%) → Shift+R (recall), флаг;
      * бой кончился → Shift+R (recall).

    Публичное:
      ensure_attack(state, focus_name) — выпустить/атаковать текущий фокус.
      recall(state)                    — Shift+R + вернуть True, если ещё в космосе.
      update_hp_guard(state)           — отслеживание урона (recall при < 50%).
    """

    def __init__(self, input_ctrl: InputController, cfg: Dict[str, Any],
                 verbose_getter=None):
        self.input = input_ctrl
        self.cfg = cfg
        self._verbose = verbose_getter or (lambda: False)
        self._last_f_name: Optional[str] = None
        self._last_f_ts: float = 0.0
        self._last_launch_ts: float = 0.0
        self._last_recall_ts: float = 0.0
        self._prev_hp: Dict[str, float] = {}
        self._last_hp_check_ts: float = 0.0

    # ---------- публичный API ----------

    def ensure_attack(self, state, focus_name: Optional[str]) -> None:
        """Выпустить дронов и отправить на цель фокуса."""
        if not focus_name:
            return
        now = time.monotonic()

        in_bay = int(getattr(state, 'drones_in_bay', 0) or 0)
        in_space = int(getattr(state, 'drones_in_space', 0) or 0)

        # 1) Выпуск
        if in_bay > 0 and in_space == 0:
            if now - self._last_launch_ts < 2.0:
                return
            self._last_launch_ts = now
            print(f"[Drone] Выпуск: {in_bay} в ангаре (Shift+F)")
            self.input.shift_press('f')
            return

        # 2) Нет дронов вообще
        if in_bay == 0 and in_space == 0:
            return

        # 3) Дроны в космосе — послать на фокус
        if in_space > 0:
            same_focus = (norm_name(focus_name) == norm_name(self._last_f_name))
            if not same_focus:
                print(f"[Drone] Атака '{focus_name}' (F)")
                self.input.press_physical('f')
                self._last_f_name = focus_name
                self._last_f_ts = now
                return
            # повтор по таймауту, только если дроны не воюют
            if not getattr(state, 'drones_fighting', False) and \
                    now - self._last_f_ts >= self.cfg['drone_f_repress_cooldown']:
                print(f"[Drone] Дроны не атакуют — повтор F")
                self.input.press_physical('f')
                self._last_f_ts = now

    def recall(self, state) -> bool:
        """Shift+R. True — если дроны ещё в космосе (или в процессе возврата)."""
        now = time.monotonic()
        in_space = int(getattr(state, 'drones_in_space', 0) or 0)
        if in_space == 0:
            return False
        if now - self._last_recall_ts < 3.0:
            return True   # уже собрали только что, ждём
        print(f"[Drone] Сбор дронов: {in_space} в космосе (Shift+R)")
        self.input.shift_press('r')
        self._last_recall_ts = now
        return True

    def on_focus_changed(self) -> None:
        """Фокус убит — следующий ensure_attack пошлёт F на новую цель."""
        self._last_f_name = None

    def update_hp_guard(self, state) -> bool:
        """True = дрона бьют (щит < порога), надо recall.

        ВАЖНО: смотрим ТОЛЬКО щит дрона, а не сумму bar + dmg.
        В EVE droneGaugeBarDmg — это затемнение ПОВЕРХ полосы щита,
        а не отдельная полоса. Их сумма не константа, поэтому деление
        dmg/(bar+dmg) давало ложные срабатывания сразу после выпуска,
        когда у дрона щит не 100% (например, 70% после предыдущего боя) —
        и бот собирал дронов через 1-2 секунды после запуска.

        Правило: если щит дрона < threshold * 100% — собираем. Порог
        и дебаунс — из cfg. Дополнительно не собираем первые
        drone_hp_grace_sec секунд после выпуска: пока дрон летит к цели,
        его щит может быть неполным, но это ещё не атака.

        Fallback: агрегатный drones_under_attack от парсера, если
        drone_entries пусто (парсер не успел разобрать записи).
        """
        in_space = int(getattr(state, 'drones_in_space', 0) or 0)
        if in_space == 0:
            return False

        now = time.monotonic()

        # Grace-период после выпуска: не считаем «под атакой» первые N сек.
        # Пока дрон летит к цели, щит мог быть не 100% ещё до выпуска.
        since_launch = now - self._last_launch_ts
        if since_launch < self.cfg.get('drone_hp_grace_sec', 8.0):
            return False

        if now - self._last_hp_check_ts < self.cfg['drone_hp_check_interval']:
            return getattr(state, 'drones_under_attack', False)
        self._last_hp_check_ts = now

        threshold = float(self.cfg['drone_hp_recall_ratio'])   # 0..1

        entries = getattr(state, 'drone_entries', None) or []
        for d in entries:
            # Смотрим ТОЛЬКО shield_pct — парсер drone_entry различает
            # shield/armor/structure по имени родительского гейджа
            # (shieldGauge/armorGauge/structureGauge) и кладёт сюда
            # РЕАЛЬНЫЙ щит дрона 0..100.
            #
            # Раньше мы падали в fallback bar_width/(bar_width+damage_width),
            # но эти поля могли относиться к armor — отсюда ложные recall'ы.
            shield = d.get('shield_pct')
            if not isinstance(shield, (int, float)):
                continue   # нет данных о щите — не трогаем
            if shield < threshold * 100.0:
                print(f"[Drone] Щит {shield:.0f}% < {threshold*100:.0f}% у "
                      f"{d.get('name', '?')} — recall")
                return True

        # НЕ используем state.drones_under_attack как fallback: этот флаг
        # в парсере может выставляться неточно. У нас теперь есть точный
        # shield_pct в каждой записи.
        return False

# =============================================================================
# MODULE CONTROLLER
# =============================================================================

class ModuleController:
    """Клики по HUD-иконкам модулей + подтверждение из дампа.

    Проблема: после клика статус модуля в дампе обновляется с задержкой.
    Без подтверждения бот кликает дважды и выключает только что включённое.
    Решение: запоминаем ожидаемое состояние (ACTIVE/READY) и снимаем его
    ТОЛЬКО когда дамп подтвердит (или по истечении module_click_cooldown * 2).
    """

    def __init__(self, input_ctrl: InputController, cfg: Dict[str, Any],
                 verbose_getter=None):
        self.input = input_ctrl
        self.cfg = cfg
        self._verbose = verbose_getter or (lambda: False)
        self._click_ts: Dict[str, float] = {}
        self._expected: Dict[str, str] = {}     # slot -> 'ACTIVE'|'READY'
        self._prop_keep_off_ts: float = 0.0
        self._warned_repair = False
        self._warned_prop = False

    # ---------- публичный API ----------

    def prop_keep_off_until(self) -> float:
        return self._prop_keep_off_ts

    def disable_engine(self, state, off_x: int, off_y: int) -> bool:
        """Выключить двигатель перед варпом. True — успешно.

        ВАЖНО: клик по HUD закрывает открытое контекстное меню. Вызывать
        до открытия меню варпа.

        ВАЖНО 2: если двигатель уже был выключен в последние prop_keep_off_time
        секунд — не трогаем (иначе повторный клик включит его обратно).
        """
        now = time.monotonic()
        # Уже выключали недавно — не кликаем повторно, чтобы не включить обратно
        if now - self._prop_keep_off_ts < self.cfg['prop_keep_off_time']:
            return False
        for name, status in (getattr(state, 'prop_modules', {}) or {}).items():
            # _effective_status учитывает наши ожидания (после клика дамп отстаёт)
            if self._effective_status(name, status) != 'ACTIVE':
                continue
            if self._toggle_module(state, name, off_x, off_y, want='READY'):
                self._prop_keep_off_ts = now
                print(f"[Module] Двигатель {name} — выключен перед варпом")
                return True
        return False

    def ensure_modules(self, state, off_x: int, off_y: int,
                       in_combat: bool) -> None:
        """Репки всегда вкл; двигатель — только в бою, не в варпе, не в keep-off."""
        if getattr(state, 'is_docked', False) or getattr(state, 'is_loading', False):
            return

        repairers = getattr(state, 'repairers', {}) or {}
        prop_modules = getattr(state, 'prop_modules', {}) or {}

        # 1. Алерты об отсутствии модулей (1 раз + троттлинг)
        if not repairers and not self._warned_repair:
            self._warned_repair = True
            print("[Module][WARNING] Репки нет в фите — контроль ремонта невозможен")
        if not prop_modules and not self._warned_prop:
            self._warned_prop = True
            print("[Module][WARNING] Двигателя нет в фите — propulsion не найден")

        # 2. Репки — всегда ON
        for name, status in repairers.items():
            eff = self._effective_status(name, status)
            if eff == 'ACTIVE':
                continue
            if self._toggle_module(state, name, off_x, off_y, want='ACTIVE'):
                print(f"[Module] Репка {name} — включаю")
                return

        # 3. Двигатель
        if not in_combat:
            return
        if getattr(state, 'is_warp', False) or getattr(state, 'is_establishing', False):
            return
        if time.monotonic() - self._prop_keep_off_ts < self.cfg['prop_keep_off_time']:
            return
        for name, status in prop_modules.items():
            eff = self._effective_status(name, status)
            if eff == 'ACTIVE':
                continue
            if self._toggle_module(state, name, off_x, off_y, want='ACTIVE'):
                print(f"[Module] Двигатель {name} — включаю")
                return

    # ---------- внутреннее ----------

    def _effective_status(self, slot_name: str, dumped: str) -> str:
        """С учётом ожидаемого состояния (после клика дамп отстаёт).

        Ожидание снимается ТОЛЬКО когда дамп подтвердит его (dumped == exp),
        либо по истечении module_click_cooldown * 2 (страховка от зависания).
        """
        exp = self._expected.get(slot_name)
        if not exp:
            return dumped
        ts = self._click_ts.get(slot_name, 0.0)
        # Дамп подтвердил ожидаемое состояние — снимаем ожидание
        if dumped == exp:
            self._expected.pop(slot_name, None)
            return dumped
        # Дамп отстаёт, но окно ожидания ещё не истекло — верим ожиданию
        if time.monotonic() - ts < self.cfg['module_click_cooldown'] * 2:
            return exp
        # Окно истекло, дамп так и не подтвердил — доверяем дампу
        self._expected.pop(slot_name, None)
        return dumped

    def _toggle_module(self, state, slot_name: str, off_x: int, off_y: int,
                       want: str) -> bool:
        """Клик по иконке модуля. True — клик сделан, ждём подтверждения."""
        now = time.monotonic()
        last = self._click_ts.get(slot_name, 0.0)
        if now - last < self.cfg['module_click_cooldown']:
            return False
        slot = self._find_slot(state, slot_name)
        if not slot or slot.get('x') is None:
            return False
        self._click_ts[slot_name] = now
        self._expected[slot_name] = want
        cx = int((slot.get('x') or 0) + off_x)
        cy = int((slot.get('y') or 0) + off_y)
        self.input.click_raw(cx, cy)
        return True

    @staticmethod
    def _find_slot(state, slot_name: str) -> Optional[Dict]:
        for lst in (getattr(state, 'high_slots', None),
                    getattr(state, 'medium_slots', None),
                    getattr(state, 'low_slots', None)):
            for m in lst or []:
                if m.get('name') == slot_name:
                    return m
        return None


# =============================================================================
# SAFETY CONTROLLER — HP / local → отход на сейф
# =============================================================================

class SafetyController:
    """Проверяет угрозы и запускает SafetyFSM отхода на сейф.

    Публичный:
      check(state) -> Optional[str] — 'hostile'|'neutral'|'low_hp'|None
      trigger(state, reason, off_x, off_y) — начать отход
      is_active() — True, если отход уже идёт
      is_threat_active(state, reason) — активна ли ещё указанная угроза
      tick(state, off_x, off_y) — продолжить FSM
      clear() — сброс (угроза устранена)
    """

    def __init__(self, cfg: Dict[str, Any], menu: MenuController,
                 input_ctrl: InputController, verbose_getter=None):
        self.cfg = cfg
        self.menu = menu
        self.input = input_ctrl
        self._verbose = verbose_getter or (lambda: False)

        self._active = False
        self._reason = ''
        self._fsm = 'IDLE'              # IDLE | MENU_WAIT | WARP_CLICKED
        self._fsm_ts = 0.0
        self._current_spot = None
        self._prefer_dock = False
        self._last_hp_warp_ts = 0.0
        self._last_safe_warp_ts = 0.0
        self._warned_no_bookmarks = False
        self._warned_no_safe = False

    # ---------- публичный API ----------

    def is_active(self) -> bool:
        return self._active

    def reason(self) -> str:
        return self._reason

    def clear(self) -> None:
        if self._active:
            print("[Safety] Угроза устранена — продолжаю работу")
        self._active = False
        self._reason = ''
        self._fsm = 'IDLE'
        self._current_spot = None

    def check(self, state) -> Optional[str]:
        """Есть ли угроза, требующая отхода."""
        if self.cfg.get('local_control_enabled', True):
            if getattr(state, 'local_has_hostile', False):
                return 'hostile'
            if getattr(state, 'local_has_neutral', False):
                return 'neutral'
        if self._is_low_hp(state):
            return 'low_hp'
        return None

    def is_threat_active(self, state, reason: str) -> bool:
        """Активна ли ещё указанная угроза.

        Используется EveBot._step_safety для решения «пора снять Safety».
        reason: 'low_hp' | 'hostile' | 'neutral'.
        """
        if reason == 'low_hp':
            return self._is_low_hp(state)
        if reason in ('hostile', 'neutral'):
            if not self.cfg.get('local_control_enabled', True):
                return False
            return bool(state.local_has_hostile or state.local_has_neutral)
        return False

    def trigger(self, state, reason: str, off_x: int = 0, off_y: int = 0) -> None:
        """Начать процедуру отхода. Идемпотентно."""
        if self._active:
            return
        now = time.monotonic()
        if reason == 'low_hp':
            if now - self._last_hp_warp_ts < self.cfg['hp_warp_cooldown']:
                return
            self._last_hp_warp_ts = now
        print(f"[Safety] !!! {reason} — отход на сейф")
        self._active = True
        self._reason = reason
        self._fsm = 'IDLE'
        self._fsm_ts = 0.0
        self._start_warp_to_safe(state, off_x=off_x, off_y=off_y)

    def tick(self, state, off_x: int, off_y: int) -> bool:
        """Продолжение FSM отхода. True — Safety ещё активен (занимает тик)."""
        if not self._active:
            return False
        now = time.monotonic()

        if self._fsm == 'MENU_WAIT':
            if getattr(state, 'menu_open', False) and (state.menu_items or []):
                if now - self._fsm_ts < self.cfg['menu_settle']:
                    return True
                item = self.menu.pick_dock_or_warp0(
                    state.menu_items, prefer_dock=self._prefer_dock)
                if item:
                    print(f"[Safety] Пункт '{item.get('text')}' — клик")
                    self.menu.click_item(item, off_x, off_y)
                    self._fsm = 'WARP_CLICKED'
                    self._fsm_ts = now
                    return True
                print("[Safety] Нужный пункт не найден — сброс")
                self._fsm = 'IDLE'
                self._active = False
                return False
            if now - self._fsm_ts >= self.cfg['menu_open_timeout']:
                print("[Safety] Меню не открылось — повтор ПКМ")
                # force=True: повторный ПКМ не должен блокироваться safe_warp_cooldown
                self._start_warp_to_safe(state, off_x=off_x, off_y=off_y, force=True)
            return True

        if self._fsm == 'WARP_CLICKED':
            if getattr(state, 'is_warp', False) or getattr(state, 'is_establishing', False):
                print(f"[Safety] Варп к '{self._current_spot}' подтверждён")
                # Safety НЕ выключаем — пусть think() решает, устранена ли угроза.
                self._fsm = 'IDLE'
                return True
            if now - self._fsm_ts >= self.cfg['warp_confirm_timeout']:
                print("[Safety] Варп не подтвердился — повтор")
                self._start_warp_to_safe(state, off_x=off_x, off_y=off_y, force=True)
            return True

        # IDLE: держим Safety активным, пока угроза не уйдёт (см. think)
        return True

    # ---------- внутреннее ----------

    def _is_low_hp(self, state) -> bool:
        if not self.cfg.get('hp_warp_enabled'):
            return False
        if getattr(state, 'is_docked', False) or getattr(state, 'is_loading', False):
            return False
        if getattr(state, 'is_warp', False) or getattr(state, 'is_establishing', False):
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

    def _find_safe_bookmark(self, state) -> Optional[Dict]:
        bms = getattr(state, 'bookmarks', None) or []
        if not bms:
            return None
        system_only = self.cfg.get('safe_bookmark_system_only', True)
        dock_hint = str(self.cfg.get('safe_bookmark_dock_hint', 'dock')).lower()
        safe_hint = str(self.cfg.get('safe_bookmark_name_hint', 'safe')).lower()
        dock_types = ('citadel', 'refinery', 'station', 'engineering complex',
                      'astrahus', 'fortizar', 'keepstar', 'tatara', 'raitaru',
                      'athanor', 'azbel', 'sotiyo')

        def _in_sys(b):
            return (not system_only) or (b.get('jumps', 0) == 0)

        def _is_dock(b):
            return str(b.get('type') or '').lower() in dock_types

        def _is_coord(b):
            return str(b.get('type') or '').lower() == 'coordinate'

        for b in bms:
            if _in_sys(b) and _is_dock(b) and dock_hint in str(b.get('label') or '').lower():
                return b
        for b in bms:
            if _in_sys(b) and _is_dock(b):
                return b
        for b in bms:
            if _in_sys(b) and _is_coord(b) and safe_hint in str(b.get('label') or '').lower():
                return b
        for b in bms:
            if _in_sys(b) and _is_coord(b):
                return b
        for b in bms:
            if _in_sys(b):
                return b
        return None

    def _start_warp_to_safe(self, state, off_x: int = 0, off_y: int = 0,
                            force: bool = False) -> None:
        """ПКМ по споту-букмарку → меню.

        force=True — игнорировать safe_warp_cooldown (нужно при повторе
        в MENU_WAIT/WARP_CLICKED, когда меню не открылось или варп не пошёл).
        """
        if not getattr(state, 'bookmarks', None):
            if not self._warned_no_bookmarks:
                self._warned_no_bookmarks = True
                print("[Safety][WARNING] Окно букмарков пусто — отход невозможен")
            return
        safe = self._find_safe_bookmark(state)
        if not safe:
            if not self._warned_no_safe:
                self._warned_no_safe = True
                print("[Safety][WARNING] Нет спота с jumps=0 — отход невозможен")
            return

        now = time.monotonic()
        if not force and now - self._last_safe_warp_ts < self.cfg['safe_warp_cooldown']:
            return
        self._last_safe_warp_ts = now

        bx = int(safe['x'] + off_x + 24)
        by = int(safe['y'] + off_y + safe.get('h', 22) // 2)
        stype = str(safe.get('type') or '').lower()
        dock_types = ('citadel', 'refinery', 'station', 'engineering complex',
                      'astrahus', 'fortizar', 'keepstar', 'tatara', 'raitaru',
                      'athanor', 'azbel', 'sotiyo')
        self._prefer_dock = stype in dock_types
        print(f"[Safety] ПКМ по '{safe.get('label')}' (jumps={safe.get('jumps')}, "
              f"type={safe.get('type')}) → "
              f"{'Dock' if self._prefer_dock else 'Warp'}")
        self.input.right_click(bx, by)
        self._fsm = 'MENU_WAIT'
        self._fsm_ts = now
        self._current_spot = safe.get('label')


# =============================================================================
# MISSION FSM — полёт по аномалиям из probe scanner
# =============================================================================

class MissionFSM:
    """IDLE → MENU_WAIT → SUBMENU_WAIT → WARP_CLICKED.

    Публичный:
      tick(state, off_x, off_y) -> bool  — True, если миссия занимает тик
      state_name() -> str
      current_anom() -> Optional[str]
      mark_arrival() — фиксация прибытия (обновляет _visited)
      reset() — сброс
    """

    def __init__(self, cfg: Dict[str, Any], menu: MenuController,
                 input_ctrl: InputController, modules: ModuleController,
                 verbose_getter=None):
        self.cfg = cfg
        self.menu = menu
        self.input = input_ctrl
        self.modules = modules
        self._verbose = verbose_getter or (lambda: False)

        self._fsm = 'IDLE'
        self._fsm_ts = 0.0
        self._current_anom: Optional[str] = None
        self._attempts = 0
        self._submenu_seen_ts: Optional[float] = None
        self._visited: Dict[str, float] = {}
        self._arrival_ts = 0.0

    # ---------- публичный API ----------

    def state_name(self) -> str:
        return self._fsm

    def current_anom(self) -> Optional[str]:
        return self._current_anom

    def reset(self) -> None:
        self._fsm = 'IDLE'
        self._fsm_ts = 0.0
        self._current_anom = None
        self._attempts = 0
        self._submenu_seen_ts = None
        self._arrival_ts = 0.0

    def mark_arrival(self) -> None:
        """Бот зафиксировал прибытие (NPC в овервью, варп завершён)."""
        if self._current_anom:
            self._visited[self._current_anom] = time.time()
            print(f"[Mission] Прибыли на '{self._current_anom}'")
        self.reset()

    def tick(self, state, off_x: int, off_y: int) -> bool:
        """Один шаг миссии. True — миссия занимает тик (не переходить к бою)."""
        now = time.monotonic()

        # Пропускаем, если нечего делать
        if getattr(state, 'is_docked', False) or getattr(state, 'is_loading', False):
            return False
        if getattr(state, 'drones_in_space', 0) > 0:
            return False   # сначала собрать дронов

        # 1. Подтверждение варпа
        if self._fsm == 'WARP_CLICKED':
            if getattr(state, 'is_warp', False) or getattr(state, 'is_establishing', False):
                print(f"[Mission] Варп '{self._current_anom}' подтверждён")
                self._visited[self._current_anom or '?'] = now
                self.reset()
                return True
            if now - self._fsm_ts >= self.cfg['warp_confirm_timeout']:
                self._attempts += 1
                if self._attempts >= self.cfg['warp_max_attempts']:
                    print(f"[Mission] Варп не удался ({self._attempts}) — пропуск")
                    self._visited[self._current_anom or '?'] = now
                    self.reset()
                else:
                    print(f"[Mission] Варп не подтверждён — повтор #{self._attempts + 1}")
                    self._fsm = 'IDLE'
                    self._fsm_ts = now
            return True

        # 2. Уже в варпе
        if getattr(state, 'is_warp', False) or getattr(state, 'is_establishing', False):
            return True

        # 3. Выбор аномалии
        candidates = self._anomaly_candidates(state)
        if not candidates and self._fsm == 'IDLE':
            return False   # нет аномалий — ждём

        if self._fsm == 'IDLE':
            # Глушим двигатель ДО открытия меню (иначе клик по HUD закроет меню)
            self.modules.disable_engine(state, off_x, off_y)
            anom = candidates[0]
            ax = int((anom.get('x') or 0) + off_x + 10)
            ay = int((anom.get('y') or 0) + off_y + max(8, (anom.get('h') or 16) // 2))
            self._current_anom = str(anom.get('name') or anom.get('id') or '?')
            print(f"[Mission] Аномалия '{self._current_anom}' "
                  f"({anom.get('group')}) — ПКМ")
            self.input.right_click(ax, ay)
            self._fsm = 'MENU_WAIT'
            self._fsm_ts = now
            return True

        if self._fsm == 'MENU_WAIT':
            if getattr(state, 'menu_open', False) and (state.menu_items or []):
                if now - self._fsm_ts < self.cfg['menu_settle']:
                    return True
                item = self.menu.pick_warp_item(state.menu_items)
                if item:
                    print(f"[Mission] Пункт '{item.get('text')}' — клик")
                    self.menu.click_item(item, off_x, off_y)
                    if item.get('has_submenu'):
                        self._fsm = 'SUBMENU_WAIT'
                        self._submenu_seen_ts = None
                    else:
                        self._fsm = 'WARP_CLICKED'
                    self._fsm_ts = now
                    return True
                print("[Mission] Warp-пункт не найден — закрываю меню")
                w = getattr(state, 'window_width', 0) or 1445
                h = getattr(state, 'window_height', 0) or 1001
                self.input.click(off_x + int(w * 0.55), off_y + int(h * 0.30))
                self.reset()
                return True
            if now - self._fsm_ts >= self.cfg['menu_open_timeout']:
                print("[Mission] Меню не открылось — повтор")
                self._fsm = 'IDLE'
                self._fsm_ts = now
            return True

        if self._fsm == 'SUBMENU_WAIT':
            item = self.menu.pick_distance_item(state.menu_items or [])
            if item:
                if self._submenu_seen_ts is None:
                    self._submenu_seen_ts = now
                    return True
                if now - self._submenu_seen_ts >= self.cfg['submenu_settle']:
                    print(f"[Mission] Дистанция '{item.get('text')}' — клик")
                    self.menu.click_item(item, off_x, off_y)
                    self._fsm = 'WARP_CLICKED'
                    self._fsm_ts = now
                return True
            # Подменю не поймали — варп мог уйти сразу
            self._submenu_seen_ts = None
            if now - self._fsm_ts >= self.cfg['menu_open_timeout']:
                print("[Mission] Подменю не найдено — жду подтверждения варпа")
                self._fsm = 'WARP_CLICKED'
                self._fsm_ts = now
            return True

        return False

    # ---------- внутреннее ----------

    def _anomaly_candidates(self, state) -> List[Dict]:
        now = time.time()
        flt = str(self.cfg.get('anomaly_group_filter') or '').lower()
        out = []
        for r in (getattr(state, 'scan_results', None) or []):
            name = str(r.get('name') or '')
            group = str(r.get('group') or '')
            if not name and not group:
                continue
            blob = f"{name} {group}".lower()
            if flt and flt not in blob:
                continue
            key = name or group
            if now - self._visited.get(key, 0) < self.cfg['anomaly_revisit_cooldown']:
                continue
            if r.get('x') is None or r.get('y') is None:
                continue
            out.append(r)
        return out

# =============================================================================
# COMBAT FSM — единый боевой автомат
# =============================================================================

class CombatFSM:
    """SELECT → ORBIT_WAIT → ORBIT_CLICK → APPROACH → LOCK_CLICK → LOCK_WAIT → ATTACK → FOCUS_DEAD → IDLE.

    Источники истины:
      * self.focus — имя текущей цели фокуса (единственное место выбора);
      * self.focus_class — класс NPC из overview_entries;
      * self.step — состояние FSM;
      * self.step_ts — момент входа в состояние.

    После включения орбиты FSM уходит в APPROACH и ЖДЁТ, пока цель
    войдёт в зону лока (distance <= lock_range * approach_lock_range_ratio).
    Только тогда делает Ctrl+клик. Если лок не удался, а цель всё ещё
    в зоне — повтор; если вышла из зоны — возврат в APPROACH.
    """

    # --- длины/пороги ---
    MAX_FOCUS_DIST_RATIO = 1.0   # цель, ушедшая за lock_range, снимается с фокуса

    def __init__(self, cfg: Dict[str, Any], input_ctrl: InputController,
                 drones: DroneController, modules: ModuleController,
                 verbose_getter=None):
        self.cfg = cfg
        self.input = input_ctrl
        self.drones = drones
        self.modules = modules
        self._verbose = verbose_getter or (lambda: False)

        # FSM
        self.step: str = 'IDLE'
        self.step_ts: float = 0.0
        self.focus: Optional[str] = None
        self.focus_class: Optional[str] = None
        self.focus_coords: Optional[Tuple[float, float]] = None

        # счётчики / таймеры
        self._lock_attempts: int = 0
        self._entry_attempts: int = 0
        self._skip_coords: Dict[str, float] = {}   # имя → ts бана (60 сек)
        self._last_focus_click_ts: float = 0.0     # клик по строке лока фокуса
        self._last_lockup_ts: float = 0.0          # до-захват новых целей
        self._last_gun_press: float = 0.0
        self._focus_switch_ts: float = 0.0
        self._just_exited_warp = False
        self._combat_start_ts = 0.0
        # Для APPROACH: последняя перепроверка орбиты
        self._last_orbit_check_ts: float = 0.0

    # ---------- свойства для обратной совместимости с main.py ----------

    @property
    def _combat_step(self) -> str:
        return self.step

    @_combat_step.setter
    def _combat_step(self, value: str) -> None:
        self.step = value

    @property
    def _lock_queue(self) -> list:
        return []

    @_lock_queue.setter
    def _lock_queue(self, value) -> None:
        pass

    # ---------- публичный API ----------

    def on_warp_exit(self) -> None:
        """Вызывается из EveBot в тике выхода из варпа."""
        self._just_exited_warp = True

    def is_active(self, state) -> bool:
        """Есть ли боевые цели — бот должен заниматься боем."""
        return bool(self._has_hostile_npcs(state))

    def tick(self, state, off_x: int, off_y: int) -> None:
        """Один шаг FSM. Все переходы — здесь."""
        now = time.monotonic()

        # 0. Прилёт — settle-пауза
        if self._just_exited_warp:
            self._just_exited_warp = False
            self._combat_start_ts = now
            print(f"[Combat] Выход из варпа — settle {self.cfg['warp_exit_settle']:.1f} с")
        if (self._combat_start_ts > 0 and
                now - self._combat_start_ts < self.cfg['warp_exit_settle']):
            return

        # 1. Обновить живой фокус (мёртв / вышел за радиус)
        self._refresh_focus(state)

        # 2. Диспетчер состояний
        if self.step == 'IDLE':
            self._enter_select(state, off_x, off_y, now)
        elif self.step == 'SELECT':
            self._tick_select(state, off_x, off_y, now)
        elif self.step == 'ORBIT_WAIT':
            self._tick_orbit_wait(state, off_x, off_y, now)
        elif self.step == 'ORBIT_CLICK':
            self._tick_orbit_click(state, off_x, off_y, now)
        elif self.step == 'APPROACH':
            self._tick_approach(state, off_x, off_y, now)
        elif self.step == 'LOCK_CLICK':
            self._tick_lock_click(state, off_x, off_y, now)
        elif self.step == 'LOCK_WAIT':
            self._tick_lock_wait(state, off_x, off_y, now)
        elif self.step == 'ATTACK':
            self._tick_attack(state, off_x, off_y, now)
        elif self.step == 'FOCUS_DEAD':
            self._tick_focus_dead(state, off_x, off_y, now)
        else:
            self._reset_to_idle()

    def on_safety_recall(self, state) -> bool:
        """Сбор дронов перед отходом. True — ещё в космосе."""
        return self.drones.recall(state)

    # ---------- FSM: состояния ----------

    def _enter_select(self, state, off_x, off_y, now) -> None:
        cand = self._lock_candidates(state)
        if not cand:
            return   # некого локнуть — ждём
        first = cand[0]
        coords = (float(first['x']), float(first['y']))
        # Страховка: не кликать в (0, 0) — это левый верхний угол окна
        if not (coords[0] or coords[1]):
            return
        self.focus = str(first['name'])
        self.focus_class = first.get('npc_class')
        self.focus_coords = coords
        self._lock_attempts = 0
        self._entry_attempts = 0
        self.drones.on_focus_changed()
        print(f"[Combat] Цель -> '{self.focus}' "
              f"({self._fmt_dist(first.get('distance'))}, {self.focus_class or '?'})")
        self._set_step('SELECT', now)
        # Клик по строке овервью (выделение, откроет SelectedItemWnd)
        self.input.click(int(self.focus_coords[0] + off_x),
                         int(self.focus_coords[1] + off_y))

    def _tick_select(self, state, off_x, off_y, now) -> None:
        if now - self.step_ts < self.cfg['select_wait']:
            return
        self.input.w_down()
        self._set_step('ORBIT_WAIT', now)

    def _tick_orbit_wait(self, state, off_x, off_y, now) -> None:
        if now - self.step_ts > self.cfg['orbit_hint_timeout']:
            print(f"[Combat] '{self.focus}' — EVE не дала 'Click target' "
                  f"за {self.cfg['orbit_hint_timeout']:.1f}с")
            self.input.w_up()
            self._ban_focus('orbit_hint_timeout')
            return
        if not getattr(state, 'orbit_hint_active', False):
            return
        self._set_step('ORBIT_CLICK', now)

    def _tick_orbit_click(self, state, off_x, off_y, now) -> None:
        """W зажат, EVE ждёт клик по цели. Клик = орбита.

        После клика переходим в APPROACH — ждём сближения без лока.
        """
        if not self.focus_coords:
            self.input.w_up()
            self._reset_to_idle()
            return
        x = int(self.focus_coords[0] + off_x)
        y = int(self.focus_coords[1] + off_y)
        self.input.w_click(x, y, hold_after=self.cfg['orbit_w_hold_after_hint'])
        print(f"[Combat] '{self.focus}' — W+клик (орбита), жду сближения")
        self._set_step('APPROACH', now)

    def _tick_approach(self, state, off_x, off_y, now) -> None:
        """Орбита активна. Ждём, пока цель войдёт в зону лока.

        Логика:
          * Сначала находим цель в овервью (актуальная дистанция и коорд.)
            и считаем зону лока. Если цель УЖЕ в зоне — сразу LOCK_CLICK,
            БЕЗ проверки таймаута (таймаут неактуален, если цель достигнута).
          * Таймаут: если цель НЕ в зоне и прошло > approach_timeout —
            баним цель (кроме случая, когда она всё-таки успела войти
            в зону — тогда лок, а не бан).
          * Пока не в зоне и таймаут не вышел — перепроверяем орбиту:
            если слетела, повторяем W+клик.
        """
        if not self.focus:
            self._reset_to_idle()
            return

        # 1. Ищем цель в овервью (по имени) — нужна до проверки таймаута,
        #    чтобы понять: цель в зоне или нет.
        entry = self._find_in_overview(state, self.focus)
        if entry is None:
            print(f"[Combat] '{self.focus}' пропал из овервью — бан")
            self._ban_focus('lost_in_overview')
            return

        dist = float(entry.get('distance') or 0.0)
        ex, ey = entry.get('x'), entry.get('y')
        if ex is not None and ey is not None:
            self.focus_coords = (float(ex), float(ey))

        # Зона лока: distance <= lock_range * ratio
        limit_km = getattr(state, 'lock_range_km', None)
        if not limit_km:
            limit_km = self.cfg.get('default_lock_range_km', 70)
        lock_zone_m = float(limit_km) * 1000.0 * \
            float(self.cfg.get('approach_lock_range_ratio', 0.9))

        # Дистанция 0 = парсер не распарсил — считаем, что в зоне.
        in_zone = (dist <= 0) or (dist <= lock_zone_m)

        # 2. Цель УЖЕ в зоне — лок, таймаут не проверяем.
        #    Это важно: если цель 40 км, а lock_zone = 63 км — она уже
        #    в зоне, и мы локнем сразу, независимо от того, сколько
        #    секунд прошло в APPROACH.
        if in_zone:
            print(f"[Combat] '{self.focus}' в зоне лока "
                  f"({self._fmt_dist(dist)} <= {lock_zone_m/1000:.0f} km) — лок")
            self._set_step('LOCK_CLICK', now)
            return

        # 3. Цель НЕ в зоне. Проверяем таймаут.
        #    Если цель в момент таймаута в зоне — мы бы уже вышли на шаге 2.
        #    Значит, здесь она реально далеко — баним.
        if now - self.step_ts > self.cfg['approach_timeout']:
            print(f"[Combat] '{self.focus}' — таймаут сближения "
                  f"({self.cfg['approach_timeout']:.0f}с, "
                  f"дист {self._fmt_dist(dist)} > {lock_zone_m/1000:.0f} km), бан")
            self._ban_focus('approach_timeout')
            return

        # 4. Орбита слетела? Проверяем по state.is_orbiting.
        if not getattr(state, 'is_orbiting', False):
            if now - self._last_orbit_check_ts >= \
                    self.cfg.get('orbit_recheck_cooldown', 6.0):
                self._last_orbit_check_ts = now
                if self.focus_coords:
                    x = int(self.focus_coords[0] + off_x)
                    y = int(self.focus_coords[1] + off_y)
                    print(f"[Combat] '{self.focus}' — орбита слетела, повтор W+клик")
                    self.input.w_click(x, y)
        else:
            self._last_orbit_check_ts = now
            
    def _tick_lock_click(self, state, off_x, off_y, now) -> None:
        if not self.focus_coords:
            self._reset_to_idle()
            return
        x = int(self.focus_coords[0] + off_x)
        y = int(self.focus_coords[1] + off_y)
        self.input.ctrl_click(x, y)
        print(f"[Combat] '{self.focus}' — Ctrl+клик (лок)")
        self._lock_attempts += 1
        self._set_step('LOCK_WAIT', now)

    def _tick_lock_wait(self, state, off_x, off_y, now) -> None:
        # Успех: цель появилась в state.targets
        if self._focus_in_targets(state):
            print(f"[Combat] '{self.focus}' — в локах")
            self._set_step('ATTACK', now)
            return
        # Таймаут
        if now - self.step_ts >= self.cfg['lock_timeout']:
            if self._lock_attempts >= self.cfg['lock_max_attempts']:
                print(f"[Combat] '{self.focus}' не локается "
                      f"({self._lock_attempts} попыток) — бан 60с")
                self._ban_focus('lock_failed')
                return
            # Цель ушла из зоны лока? Возврат в APPROACH, чтобы не долбить
            # Ctrl+клик вслепую.
            entry = self._find_in_overview(state, self.focus)
            if entry is not None:
                dist = float(entry.get('distance') or 0.0)
                limit_km = getattr(state, 'lock_range_km', None)
                if not limit_km:
                    limit_km = self.cfg.get('default_lock_range_km', 70)
                lock_zone_m = float(limit_km) * 1000.0 * \
                    float(self.cfg.get('approach_lock_range_ratio', 0.9))
                if dist > 0 and dist > lock_zone_m:
                    print(f"[Combat] '{self.focus}' вышел из зоны лока "
                          f"({self._fmt_dist(dist)}) — возврат в APPROACH")
                    self._set_step('APPROACH', now)
                    return
            print(f"[Combat] '{self.focus}' не в локах — повтор "
                  f"#{self._lock_attempts + 1}")
            self._set_step('LOCK_CLICK', now)

    def _tick_attack(self, state, off_x, off_y, now) -> None:
        # 1. Активировать фокус (клик по строке лока, если он не активен)
        if not self._ensure_active(state, off_x, off_y):
            return   # кликнули — ждём следующего тика
        # 2. Дроны — выпуск/атака/recall
        if self.drones.update_hp_guard(state):
            self.drones.recall(state)
            return
        self.drones.ensure_attack(state, self.focus)
        # 3. Пушки
        self._press_guns(state, now)
        # 4. До-захват новых целей до lock_batch_size
        self._maintain_locks(state, off_x, off_y, now)
        # 5. Фокус мёртв?
        if not self._focus_in_targets(state):
            self._set_step('FOCUS_DEAD', now)

    def _tick_focus_dead(self, state, off_x, off_y, now) -> None:
        print(f"[Combat] Фокус '{self.focus}' убит")
        self.drones.on_focus_changed()
        self.focus = None
        self.focus_class = None
        self.focus_coords = None
        if now - self._focus_switch_ts < self.cfg['focus_switch_cooldown']:
            return
        self._focus_switch_ts = now
        self._reset_to_idle()

    # ---------- внутреннее ----------

    def _reset_to_idle(self) -> None:
        self.step = 'IDLE'
        self.step_ts = time.monotonic()
        self._lock_attempts = 0

    def _set_step(self, step: str, now: float) -> None:
        self.step = step
        self.step_ts = now

    def _ban_focus(self, why: str) -> None:
        if self.focus:
            self._skip_coords[self.focus.lower()] = time.time()
            print(f"[Combat] Бан '{self.focus}' на 60с ({why})")
        self.input.w_up()
        self.focus = None
        self.focus_class = None
        self.focus_coords = None
        self._last_orbit_check_ts = 0.0
        self._reset_to_idle()

    def _find_in_overview(self, state, name: str) -> Optional[Dict]:
        """Найти запись в overview_entries по нормализованному имени."""
        if not name:
            return None
        fn = norm_name(name)
        for e in (getattr(state, 'overview_entries', None) or []):
            if norm_name(e.get('name')) == fn:
                return e
        return None

    def _refresh_focus(self, state) -> None:
        """Сбросить фокус, если он мёртв или ушёл слишком далеко.

        ВАЖНО: в состояниях SELECT / ORBIT_WAIT / ORBIT_CLICK / APPROACH
        мы как раз сближаемся с целью. Сбрасывать фокус по дистанции в
        этих состояниях нельзя — иначе цель вне lock_range сбрасывается
        до того, как FSM успеет её догнать.

        В ATTACK / LOCK_WAIT / LOCK_CLICK — сбрасываем, если цель ушла
        за lock_range * MAX_FOCUS_DIST_RATIO.
        """
        if not self.focus:
            return
        if self.step in ('SELECT', 'ORBIT_WAIT', 'ORBIT_CLICK', 'APPROACH'):
            return  # сближаемся — не трогаем
        limit_km = getattr(state, 'lock_range_km', None)
        if not limit_km:
            return
        limit_m = float(limit_km) * 1000.0 * self.MAX_FOCUS_DIST_RATIO
        for e in (getattr(state, 'overview_entries', None) or []):
            if norm_name(e.get('name')) == norm_name(self.focus):
                dist = float(e.get('distance') or 0.0)
                if dist > limit_m:
                    print(f"[Combat] Фокус '{self.focus}' вышел за "
                          f"{limit_km} км — сброс")
                    self._ban_focus('out_of_range')
                return
            
    def _focus_in_targets(self, state) -> bool:
        if not self.focus:
            return False
        fn = norm_name(self.focus)
        return any(norm_name(t.get('name')) == fn
                   for t in (state.targets or []))

    def _ensure_active(self, state, off_x, off_y) -> bool:
        """True — фокус уже активен; False — сделали клик, ждём тика."""
        if not self.focus:
            return True
        fn = norm_name(self.focus)
        focus_t = None
        for t in (state.targets or []):
            if norm_name(t.get('name')) == fn:
                focus_t = t
                break
        if focus_t is None:
            return True
        active = getattr(state, 'active_target', None)
        if focus_t.get('is_active') or \
                norm_name((active or {}).get('name')) == fn:
            return True
        # Клик по строке лока фокуса (анти-спам 1.5 сек, свой таймер)
        now = time.monotonic()
        if now - self._last_focus_click_ts < 1.5:
            return False
        print(f"[Combat] Активирую фокус '{self.focus}' (клик по строке лока)")
        tx = int((focus_t.get('x') or 0) + off_x)
        ty = int((focus_t.get('y') or 0) + off_y)
        self.input.click_raw(tx, ty)
        self._last_focus_click_ts = now
        return False

    def _press_guns(self, state, now: float) -> None:
        if not self.cfg.get('auto_attack'):
            return
        if not state.targets:
            return
        if not self._focus_in_targets(state):
            return
        # Цель уже под огнём?
        if self._target_under_fire(state):
            return
        if now - self._last_gun_press < self.cfg['attack_cooldown']:
            return
        for slot in (getattr(state, 'high_slots', None) or []):
            if slot.get('active'):
                continue
            m = re.search(r'(\d+)', str(slot.get('name') or ''))
            if not m:
                continue
            key = f"f{int(m.group(1)) + self.cfg.get('slot_hotkey_offset', 0)}"
            if self.input.press(key, cooldown=self.cfg['attack_cooldown']):
                print(f"[Combat] Атака {slot.get('name')} ({key.upper()})")
                self._last_gun_press = now
                return

    def _target_under_fire(self, state) -> bool:
        """True = цель уже под нашим огнём (не жать F1 повторно).

        Если цель НЕ найдена в овервью — считаем «под огнём», чтобы не
        выключить оружие повторным F1 (в EVE это тоггл).
        """
        if not self.focus:
            return True
        fn = norm_name(self.focus)
        found = False
        for e in (getattr(state, 'overview_entries', None) or []):
            if norm_name(e.get('name')) == fn:
                found = True
                if e.get('targeted_by_me'):
                    return True
                break
        if not found:
            return True
        active = getattr(state, 'active_target', None) or {}
        if norm_name(active.get('name')) == fn and active.get('targeted_by_me'):
            return True
        return False

    def _maintain_locks(self, state, off_x, off_y, now: float) -> None:
        max_locks = int(self.cfg.get('lock_batch_size', 3))
        locked = list(state.targets or [])
        if len(locked) >= max_locks:
            return
        need = max_locks - len(locked)
        # Для до-захвата фильтр по lock_range ОБЯЗАТЕЛЕН: Ctrl+клик по
        # цели вне зоны лока всё равно не сработает.
        cand = self._lock_candidates(state, exclude_locked=True,
                                     apply_range_filter=True)
        if not cand:
            return
        
        if now - self._last_lockup_ts < self.cfg['lockup_cooldown']:
            return
        self._last_lockup_ts = now
        new = cand[:need]
        print(f"[Combat] До-захват {len(new)} целей (Ctrl+клик)")
        for c in new:
            x = int(c['x'] + off_x)
            y = int(c['y'] + off_y)
            self.input.ctrl_click(x, y)
            time.sleep(self.cfg['lock_batch_delay'])

    def _lock_candidates(self, state, exclude_locked: bool = False,
                         apply_range_filter: bool = False) -> List[Dict]:
        """Кандидаты на захват.

        apply_range_filter=False (по умолчанию) — отдаём ВСЕ боевые цели
        из овервью, включая дальние. Нужно для выбора ФОКУСА: чтобы
        APPROACH мог сблизиться с целью за пределами lock_range.

        apply_range_filter=True — фильтруем по lock_range * 0.95.
        Нужно для ДО-захвата (_maintain_locks): захватывать за пределами
        лока всё равно не получится, Ctrl+клик не сработает.
        """
        now = time.time()
        skip = {n.lower() for n, ts in self._skip_coords.items()
                if now - ts < 60.0}
        limit_km = getattr(state, 'lock_range_km', None)
        if not limit_km:
            limit_km = self.cfg.get('default_lock_range_km', 70)
        limit_m = float(limit_km) * 1000.0 * 0.95

        locked_names = set()
        if exclude_locked:
            for t in (state.targets or []):
                locked_names.add(norm_name(t.get('name')))

        env_types = ('station', 'stargate', 'planet', 'asteroid',
                     'citadel', 'wormhole')
        frig, bs, rest = [], [], []
        for e in (getattr(state, 'overview_entries', None) or []):
            name = str(e.get('name') or '').strip()
            if not name:
                continue
            if str(e.get('type') or '').lower() in env_types:
                continue
            if name.lower() in skip:
                continue
            if exclude_locked and norm_name(name) in locked_names:
                continue
            if e.get('targeted_by_me'):
                continue
            x, y = e.get('x'), e.get('y')
            if x is None or y is None:
                continue
            dist = float(e.get('distance') or 0.0)
            if apply_range_filter and limit_m and dist > 0 and dist > limit_m:
                continue
            item = {
                'name': name, 'x': float(x), 'y': float(y),
                'distance': dist, 'npc_class': e.get('npc_class'),
            }
            cls = e.get('npc_class')
            if cls == 'FRIGATE':
                frig.append(item)
            elif cls == 'BATTLESHIP':
                bs.append(item)
            else:
                rest.append(item)

        frig.sort(key=lambda c: c['distance'])
        bs.sort(key=lambda c: c['distance'])
        rest.sort(key=lambda c: c['distance'])

        if self.cfg.get('post_frigate_priority') == 'random':
            after = bs + rest
            random.shuffle(after)
            return frig + after
        return frig + bs + rest
    
    def _has_hostile_npcs(self, state) -> bool:
        env_types = ('station', 'stargate', 'planet', 'asteroid',
                     'citadel', 'wormhole')
        for e in (getattr(state, 'overview_entries', None) or []):
            if e.get('npc_class'):
                return True
            if e.get('npc_class') is None and \
                    str(e.get('type') or '').lower() not in env_types:
                if e.get('name'):
                    return True
        return bool(getattr(state, 'npc_all', None))

    @staticmethod
    def _fmt_dist(meters) -> str:
        try:
            m = float(meters or 0.0)
        except (TypeError, ValueError):
            return "?"
        return f"{m:.0f} m" if m < 1000 else f"{m / 1000:.1f} km"
    
# =============================================================================
# EVE BOT — тонкий фасад
# =============================================================================

class EveBot:
    """Публичный API для main.py.

    Внутри — композиция подсистем. think() — тонкий pipeline из шагов,
    каждый шаг возвращает True, если он «обработал тик» (дальше не идём).
    """

    def __init__(self, stats=None):
        self.verbose: bool = False

        # --- cfg: единый словарь, main.py правит его как раньше ---
        self.cfg: Dict[str, Any] = dict(DEFAULT_CFG)

        # --- подсистемы ---
        self._vget = lambda: self.verbose
        self.input = InputController(self.cfg, self._vget)
        self.windows = WindowManager(self._vget)
        self.menu = MenuController(self.input, self.cfg)
        self.drones = DroneController(self.input, self.cfg, self._vget)
        self.modules = ModuleController(self.input, self.cfg, self._vget)
        self.safety = SafetyController(self.cfg, self.menu, self.input, self._vget)
        self.mission = MissionFSM(self.cfg, self.menu, self.input,
                                  self.modules, self._vget)
        self.combat = CombatFSM(self.cfg, self.input, self.drones,
                                self.modules, self._vget)

        # --- публичный доступ к окну (для main.py) ---
        self.window_name: Optional[str] = None

        # --- кооперативная остановка ---
        self._stop_requested = False
        self._modal_attempts = 0
        self._undocking = False
        self._undock_start_ts = 0.0
        self._undock_complete_ts = 0.0
        self._last_undock_ts = 0.0
        self._probe_open_ts = 0.0
        self._probe_opening = False
        self._probe_attempts = 0

        # --- трекинг варпа (для settle после выхода) ---
        self._last_was_warp = False

        # --- статистика сессии (общая с main.py) ---
        self.stats = stats if stats is not None else {
            'sites_cleared': 0, 'kills': 0, 'drones_lost': 0, 'escapes': 0,
        }

        # --- трекинг статистики ---
        self._prev_locked_count = 0
        self._had_npcs = False
        self._drone_baseline: Optional[int] = None
        self._low_drones_alerted = False

        # --- state (для совместимости; main.py может читать) ---
        self.current_state = None

    # =========================================================================
    # ПУБЛИЧНЫЙ API (сохранён)
    # =========================================================================

    def set_window(self, title: str) -> None:
        self.window_name = str(title)
        self.windows.set_window(title)

    def list_eve_windows(self) -> list:
        return self.windows.list_eve_windows()

    def request_stop(self) -> None:
        self._stop_requested = True
        print("[Bot] STOP: завершаю текущие действия, новые не начинаю")

    def reset_session_state(self) -> None:
        """Полный сброс боевого/миссионного/безопасного состояния.

        Вызывать при старте бота из GUI. Эквивалент ручного сброса
        полей в старой версии main.py.
        """
        self._stop_requested = False
        self._modal_attempts = 0
        self._undocking = False
        self._undock_start_ts = 0.0
        self._undock_complete_ts = 0.0
        self._probe_open_ts = 0.0
        self._probe_opening = False
        self._probe_attempts = 0
        self._last_was_warp = False
        self._prev_locked_count = 0
        self._had_npcs = False
        self._drone_baseline = None
        self._low_drones_alerted = False

        self.safety.clear()
        self.mission.reset()
        self.combat.step = 'IDLE'
        self.combat.step_ts = 0.0
        self.combat.focus = None
        self.combat.focus_class = None
        self.combat.focus_coords = None
        self.combat._skip_coords.clear()
        self.combat._just_exited_warp = False
        self.combat._combat_start_ts = 0.0
        self.combat._lock_attempts = 0
        self.combat._entry_attempts = 0
        self.combat._last_focus_click_ts = 0.0
        self.combat._last_lockup_ts = 0.0
        self.combat._last_orbit_check_ts = 0.0
        self.drones._last_f_name = None
        self.drones._last_f_ts = 0.0

    def apply_action(self, action: dict) -> bool:
        """Приём команды от NN-канала. Пока реализованы EMERGENCY_EXIT и IDLE."""
        if not isinstance(action, dict):
            return False
        a = str(action.get('action') or 'IDLE')
        if a == 'EMERGENCY_EXIT':
            reason = action.get('reason') or 'NN decision'
            self.stats['escapes'] += 1
            print(f"[Bot][NN] EMERGENCY_EXIT: {reason}")
            self.safety._active = False
            return True
        if a == 'IDLE':
            return True
        print(f"[Bot][NN] action={a} — не реализовано")
        return False

    # =========================================================================
    # СВОЙСТВА ДЛЯ ОБРАТНОЙ СОВМЕСТИМОСТИ С main.py
    # =========================================================================

    @property
    def _mission(self) -> str:
        if self.safety.is_active():
            return 'SAFE_MENU_WAIT' if self.safety._fsm == 'MENU_WAIT' else 'SAFE'
        return self.mission.state_name()

    @property
    def _current_anom(self) -> Optional[str]:
        return self.mission.current_anom()

    @property
    def _just_exited_warp(self) -> bool:
        return self.combat._just_exited_warp

    @_just_exited_warp.setter
    def _just_exited_warp(self, value: bool) -> None:
        self.combat._just_exited_warp = bool(value)

    @property
    def _combat_start_ts(self) -> float:
        return self.combat._combat_start_ts

    @_combat_start_ts.setter
    def _combat_start_ts(self, value: float) -> None:
        self.combat._combat_start_ts = float(value or 0.0)

    @property
    def _focus_name(self) -> Optional[str]:
        return self.combat.focus

    @_focus_name.setter
    def _focus_name(self, value) -> None:
        self.combat.focus = value

    @property
    def _combat_step(self) -> str:
        return self.combat.step

    @_combat_step.setter
    def _combat_step(self, value: str) -> None:
        self.combat.step = value

    @property
    def _lock_queue(self) -> list:
        return []

    @_lock_queue.setter
    def _lock_queue(self, value) -> None:
        pass

    @property
    def _skip_coords(self) -> Dict[str, float]:
        return self.combat._skip_coords

    @property
    def _engage_pending(self) -> bool:
        return self.combat.step in ('ORBIT_WAIT', 'ORBIT_CLICK')

    @_engage_pending.setter
    def _engage_pending(self, value: bool) -> None:
        if not value and self.combat.step in ('ORBIT_WAIT', 'ORBIT_CLICK'):
            self.combat.input.w_up()
            self.combat.step = 'IDLE'
            self.combat.step_ts = 0.0

    @property
    def _engage_target_name(self) -> Optional[str]:
        return self.combat.focus

    @_engage_target_name.setter
    def _engage_target_name(self, value) -> None:
        pass   # целевое поле — combat.focus

    @property
    def _engage_coords(self):
        return self.combat.focus_coords

    @_engage_coords.setter
    def _engage_coords(self, value) -> None:
        self.combat.focus_coords = value

    @property
    def _engage_started_ts(self) -> float:
        return self.combat.step_ts

    @_engage_started_ts.setter
    def _engage_started_ts(self, value: float) -> None:
        pass   # step_ts управляется FSM

    @property
    def _engage_last_ts(self) -> float:
        return self.combat._focus_switch_ts

    @_engage_last_ts.setter
    def _engage_last_ts(self, value: float) -> None:
        self.combat._focus_switch_ts = float(value or 0.0)

    @property
    def _entry_attempts(self) -> int:
        return self.combat._entry_attempts

    @_entry_attempts.setter
    def _entry_attempts(self, value: int) -> None:
        self.combat._entry_attempts = int(value or 0)

    @property
    def _approach_target(self) -> Optional[str]:
        return self.combat.focus

    @_approach_target.setter
    def _approach_target(self, value) -> None:
        pass   # алиас фокуса

    # =========================================================================
    # ГЛАВНЫЙ ТИК
    # =========================================================================

    def think(self, state, use_drones: bool) -> None:
        """Один тик. Pipeline из шагов; каждый True → дальше не идём.

        try/finally: даже если внутри что-то бросит, finally напечатает
        'think exit' — это позволит в логе отличить «think завис» от
        «think упал с исключением».
        """
        if self._stop_requested:
            return
        self.current_state = state

        if self.verbose:
            pass
            #print("[Bot] think enter")
        try:
            if not self.windows.activate():
                return

            # Выход из варпа отслеживаем ПЕРВЫМ делом — до любых ранних
            # return, иначе settle после варпа «уезжает» на N тиков.
            self._track_warp_exit(state)

            if self._step_modal(state):     return
            if self._step_safety(state):    return
            if self._step_dock(state):      return
            if self._step_loading(state):   return
            if self._step_probe(state):     return
            if self._step_mission(state):   return
            self._step_modules(state)       # side effect, не блокирует тик
            if self._step_combat(state):    return
            self._step_idle(state)
        finally:
            if self.verbose:
                pass
                #print("[Bot] think exit")
    # -------------------------------------------------------------------------
    # ШАГИ
    # -------------------------------------------------------------------------

    def _step_modal(self, state) -> bool:
        modal = getattr(state, 'modal_info', None) or {}
        if not modal.get('open'):
            self._modal_attempts = 0
            return False
        self._modal_attempts += 1
        if self._modal_attempts <= self.cfg['modal_enter_attempts']:
            pyautogui.press('enter')
            print(f"[Modal] Enter (попытка {self._modal_attempts})")
        else:
            close_btn = modal.get('close')
            if close_btn:
                off_x, off_y = self.windows.offset()
                cx = int(close_btn['x'] + close_btn['w'] // 2 + off_x)
                cy = int(close_btn['y'] + close_btn['h'] // 2 + off_y)
                self.input.click_raw(cx, cy)
                print(f"[Modal] Close (попытка {self._modal_attempts})")
            else:
                pyautogui.press('enter')
        time.sleep(0.4)
        return True

    def _step_safety(self, state) -> bool:
        off_x, off_y = self.windows.offset()

        # 1. Продолжение уже начатого отхода
        if self.safety.is_active():
            self.safety.tick(state, off_x, off_y)
            # Проверка «угроза ушла» — не в варпе, HP восстановился, локал чист
            reason = self.safety.reason()
            still_threat = self.safety.is_threat_active(state, reason)
            if not still_threat and not (state.is_warp or state.is_establishing):
                self.safety.clear()
            return True

        # 2. Новая угроза
        reason = self.safety.check(state)
        if reason:
            self.stats['escapes'] += 1
            self.safety.trigger(state, reason, off_x=off_x, off_y=off_y)
            self.safety.tick(state, off_x, off_y)
            return True
        return False

    def _step_dock(self, state) -> bool:
        now = time.monotonic()

        # Андок завершён
        if self._undocking and not state.is_docked:
            self._undocking = False
            self._undock_complete_ts = now
            print("[Bot] Андок завершён")

        # Settle после андока
        if self._undock_complete_ts > 0 and \
                now - self._undock_complete_ts < self.cfg['undock_settle_time']:
            return True

        if not state.is_docked:
            return False

        # В доке
        if self._undocking:
            if now - self._undock_start_ts >= self.cfg['undock_timeout']:
                print("[Bot] Андок не завершился — повтор клика")
                self._undocking = False
                self._last_undock_ts = 0.0
            else:
                return True

        btn = getattr(state, 'undock_button', None)
        if not btn:
            return True
        if now - self._last_undock_ts < self.cfg['undock_cooldown']:
            return True
        self._last_undock_ts = now
        self._undock_start_ts = now
        self._undocking = True
        off_x, off_y = self.windows.offset()
        print(f"[Bot] Андок (клик Undock @ {btn['x']},{btn['y']})")
        self.input.click_raw(int(btn['x'] + off_x), int(btn['y'] + off_y))
        return True

    def _step_loading(self, state) -> bool:
        return bool(getattr(state, 'is_loading', False))

    def _step_probe(self, state) -> bool:
        """Alt+P если окно Probe Scanner закрыто.

        Ограничение: не более probe_max_attempts попыток за сессию. Если
        окно так и не открылось (у корабля нет сканера) — сдаёмся и больше
        не блокируем бой.
        """
        if getattr(state, 'probe_scanner_open', False):
            self._probe_opening = False
            self._probe_attempts = 0
            return False
        if state.is_docked or state.is_loading:
            return False
        # Больше не пытаемся — не блокируем бой
        if self._probe_attempts >= self.cfg.get('probe_max_attempts', 3):
            return False
        now = time.monotonic()
        if not self._probe_opening:
            print(f"[Bot] Probe Scanner закрыт — Alt+P "
                  f"(попытка {self._probe_attempts + 1})")
            self.input.alt_press('p')
            self._probe_opening = True
            self._probe_open_ts = now
            self._probe_attempts += 1
            return True
        if now - self._probe_open_ts >= self.cfg['probe_open_timeout']:
            self._probe_opening = False
        return True

    def _step_mission(self, state) -> bool:
        # Если есть NPC — миссия не нужна, идём в бой.
        # Сбрасываем незавершённую миссию: если мы уже открыли меню/подменю,
        # то после боя продолжать с этого места нельзя (окно закроется,
        # координаты протухнут).
        if self.combat.is_active(state):
            if self.mission.state_name() != 'IDLE':
                self.mission.reset()
            return False
        off_x, off_y = self.windows.offset()
        return self.mission.tick(state, off_x, off_y)

    def _step_modules(self, state) -> None:
        """Репки/двигатель. Side effect, тик не занимает."""
        off_x, off_y = self.windows.offset()
        in_combat = self.combat.is_active(state)
        self.modules.ensure_modules(state, off_x, off_y, in_combat)

    def _step_combat(self, state) -> bool:
        if not self.combat.is_active(state):
            # Бой кончился — если дроны в космосе, собираем
            if getattr(state, 'drones_in_space', 0) > 0:
                self.drones.recall(state)
                return True
            return False

        off_x, off_y = self.windows.offset()

        # Фиксация прибытия на аномалию
        if self.mission.current_anom() and not state.is_warp and \
                not state.is_establishing:
            self.mission.mark_arrival()

        # Статистика
        self._track_kills(state)
        self._track_drones(state)

        # Боевой FSM
        self.combat.tick(state, off_x, off_y)
        return True

    def _step_idle(self, state) -> bool:
        return False

    # -------------------------------------------------------------------------
    # ТРЕКИНГ
    # -------------------------------------------------------------------------

    def _track_warp_exit(self, state) -> None:
        """Ставим флаг _just_exited_warp в тик выхода из варпа.

        Вызывается в самом начале think(), чтобы поймать переход
        'варп → не варп' независимо от того, какой шаг pipeline
        обработает этот тик.
        """
        was_warp = self._last_was_warp
        now_warp = bool(state.is_warp or state.is_establishing)
        if was_warp and not now_warp:
            self.combat.on_warp_exit()
        self._last_was_warp = now_warp

    def _track_kills(self, state) -> None:
        cur = len(state.targets or [])
        has_npcs = self.combat.is_active(state)
        if cur < self._prev_locked_count and has_npcs:
            killed = self._prev_locked_count - cur
            self.stats['kills'] += killed
            print(f"[Stats] Убито: +{killed} (всего {self.stats['kills']})")
        self._prev_locked_count = cur

    def _track_drones(self, state) -> None:
        total = int(getattr(state, 'drones_in_bay', 0) or 0) + \
                int(getattr(state, 'drones_in_space', 0) or 0)
        if total <= 0:
            return
        if self._drone_baseline is None:
            self._drone_baseline = total
            return
        if total < self._drone_baseline:
            lost = self._drone_baseline - total
            self._drone_baseline = total
            self.stats['drones_lost'] += lost
            print(f"[Stats] Потеря дронов: -{lost} (всего {self.stats['drones_lost']})")
        elif total > self._drone_baseline:
            self._drone_baseline = total
            self._low_drones_alerted = False
        if total < self.cfg['min_drones'] and not self._low_drones_alerted:
            self._low_drones_alerted = True
            print(f"[Stats] МАЛО ДРОНОВ: {total} — нужен док")


# =============================================================================
# ENTRY POINT (для ручной проверки без GUI)
# =============================================================================

if __name__ == "__main__":
    print("bot.py — модуль бота. Запускать через main.py.")

