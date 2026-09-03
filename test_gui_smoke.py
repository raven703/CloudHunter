# -*- coding: utf-8 -*-
"""Дымовой тест GUI: конструирование EveApp, баннер, статистика, кнопка дампа."""
import io
import json
import sys
from unittest.mock import MagicMock

# Сохраняем ссылку на враппер, чтобы GC не закрыл буфер при перенаправлении stdout
_out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stdout = _out

# Заглушки тяжёлых зависимостей (в этом venv их нет; у пользователя стоят)
for mod in ("pyautogui", "pygetwindow", "win32gui", "win32process", "win32api", "win32con"):
    if mod not in sys.modules:
        stub = MagicMock()
        if mod == "pygetwindow":
            stub.getWindowsWithTitle.return_value = []
        sys.modules[mod] = stub

import time as _t
_t.sleep = lambda *_: None  # без реальных пауз

import main as appmod
from new_parser import EveParserNew

import os
uiroot_path = "uiroot_new.json" if os.path.exists("uiroot_new.json") else "raw_dump_20260902_191755.json"
with open(uiroot_path, "r", encoding="utf-8") as f:
    data = json.load(f)
state, draw_list = EveParserNew().parse(data)

app = appmod.EveApp("eveui.exe")   # окно создаётся, mainloop не стартует
print("GUI constructed OK")

# Баннер на разных состояниях
app.update_banner(state)                       # COMBAT (в дампе есть цели/NPC)
state2 = state
app.last_state = state
app.update_session_stats()
app.stats['kills'] = 5
app.stats['sites_cleared'] = 2
app.update_session_stats()
print(f"banner text: {app.status_banner.cget('text')}")
print(f"stats sites: {app.stat_vars['sites'].get()}, kills: {app.stat_vars['kills'].get()}")
assert app.stat_vars['kills'].get() == '5'

# Кнопка "ВЫГРУЗИТЬ СЫРОЙ JSON"
app.client.last_raw_json = data
path = app.client.dump_raw_json()
with open(path, "r", encoding="utf-8") as f:
    check = json.load(f)
assert isinstance(check, dict)
print(f"raw dump OK: {path} ({len(json.dumps(check))} chars)")

# Завершение: возвращаем stdout/stderr ДО destroy (как делает on_exit)
sys.stdout = _out
sys.stderr = sys.__stderr__
app.root.destroy()
print("\nGUI SMOKE TEST PASSED")
