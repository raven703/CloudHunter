import tkinter as tk
from tkinter import ttk
import threading
import time
from queue import Empty
import ctypes
import sys
import os


# Твои рабочие модули
from client import EveClient
from bot import EveBot
from new_parser import STANDING_COLORS, build_debug_blocks
from state_server import StateBroadcaster, ActionReceiver

class WidgetLogger:
    def __init__(self, callback):
        self.callback = callback

    def write(self, string):
        if string.strip():
            self.callback(string.strip())

    def flush(self):
        pass

class ParserDebugWindow(tk.Toplevel):
    """Полноценное окно отладки НОВОГО парсера.

    Показывает все данные, которые извлекает EveParserNew:
    корабль, модули, дроны, цели, овервью, NPC, локальный чат,
    проб-сканер, инфо-панель, неоком, меню, статус бота и технику.

    Возможности:
      - моноширинный текст + скроллбар;
      - цветовое кодирование (danger/warn/info/good/dim);
      - кнопка REFRESH (принудительное обновление из последнего состояния);
      - чекбокс ALWAYS ON TOP;
      - чекбокс AUTO-SCROLL.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title("PARSER DEBUG")
        self.geometry("860x820+380+20")
        self.configure(bg='#0a0a0a')

        self._latest_state = None

        # --- Панель управления ---
        toolbar = tk.Frame(self, bg='#1a1a1a')
        toolbar.pack(fill=tk.X, pady=(4, 2), padx=6)

        self.btn_refresh = tk.Button(toolbar, text="REFRESH", bg='#37474f', fg='white',
                                     command=self.force_refresh, relief='flat', font=("Arial", 9, "bold"))
        self.btn_refresh.pack(side=tk.LEFT, padx=(0, 8))

        self.top_var = tk.BooleanVar(value=False)
        tk.Checkbutton(toolbar, text="ALWAYS ON TOP", variable=self.top_var, command=self._toggle_topmost,
                       bg='#1a1a1a', fg='#88ddff', selectcolor='#1a1a1a',
                       activebackground='#1a1a1a', activeforeground='#88ddff').pack(side=tk.LEFT, padx=4)

        self.auto_scroll_var = tk.BooleanVar(value=True)
        tk.Checkbutton(toolbar, text="AUTO-SCROLL", variable=self.auto_scroll_var,
                       bg='#1a1a1a', fg='#88ddff', selectcolor='#1a1a1a',
                       activebackground='#1a1a1a', activeforeground='#88ddff').pack(side=tk.LEFT, padx=4)

        # --- Текстовое поле ---
        frame = tk.Frame(self, bg='#0a0a0a')
        frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=(2, 6))

        self.text = tk.Text(frame, bg='#0a0a0a', fg='#cccccc', font=("Consolas", 9),
                            wrap='none', padx=8, pady=8, relief='flat', state='disabled')
        scroll_y = tk.Scrollbar(frame, command=self.text.yview)
        scroll_x = tk.Scrollbar(frame, orient='horizontal', command=self.text.xview)
        self.text.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.text.pack(fill=tk.BOTH, expand=True)

        # --- Цветовые теги ---
        self.text.tag_configure('title', foreground='#ffcc55', font=("Consolas", 11, "bold"))
        self.text.tag_configure('header', foreground='#88ddff', font=("Consolas", 10, "bold"))
        self.text.tag_configure('good', foreground='#88ff88')
        self.text.tag_configure('warn', foreground='#ffcc00')
        self.text.tag_configure('danger', foreground='#ff5555')
        self.text.tag_configure('info', foreground='#88ddff')
        self.text.tag_configure('dim', foreground='#777777')
        for st, color in STANDING_COLORS.items():
            self.text.tag_configure(st, foreground=color)

        # Закрытие прячет окно, а не убивает
        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        self.withdraw()

    def _toggle_topmost(self):
        try:
            self.attributes("-topmost", self.top_var.get())
        except tk.TclError:
            pass

    def set_state(self, state):
        """Принять новое состояние нового парсера и перерисовать."""
        self._latest_state = state
        self.render()

    def force_refresh(self):
        """Принудительная перерисовка из последнего сохранённого состояния."""
        if self._latest_state is not None:
            self.render()

    def render(self):
        if not self.winfo_exists():
            return
        if self._latest_state is None:
            return

        blocks = build_debug_blocks(self._latest_state)

        # Сохраняем позицию скролла, если авто-скролл выключен
        yview = self.text.yview()
        was_disabled = str(self.text.cget('state')) == 'disabled'
        self.text.config(state="normal")
        self.text.delete("1.0", tk.END)

        for tag, text in blocks:
            self.text.insert(tk.END, text + "\n", tag)

        if self.auto_scroll_var.get():
            self.text.see(tk.END)
        else:
            try:
                self.text.yview_moveto(yview[0])
            except tk.TclError:
                pass
        self.text.config(state="disabled")
        _ = was_disabled


class EveApp:
    def __init__(self, exe_path):
        # Перехватываем и ошибки, и обычный вывод        
        self.stderr_redirector = WidgetLogger(self.log_safe)
        sys.stderr = self.stderr_redirector
        sys.stdout = self.stderr_redirector  # ДОБАВЬ ЭТУ СТРОКУ

        self.root = tk.Tk()
        self.root.title("EVE Pilot Panel")
        
        win_w, win_h = 400, 950
        screen_w = self.root.winfo_screenwidth()
        self.root.geometry(f"{win_w}x{win_h}+{screen_w - win_w}+{10}")
        self.root.configure(bg='#1a1a1a')

        # --- NN CONTROL: атрибуты до build_ui (чекбокс их читает) ---
        self.nn_control_var = tk.BooleanVar(value=False)
        self._nn_last_action_ts = 0.0
        self._nn_warn_ts = 0.0

        self.build_ui()
        self.parser_debug_window = ParserDebugWindow(self.root)

        self.stderr_redirector = WidgetLogger(self.log_safe)
        sys.stderr = self.stderr_redirector

        # Статистика сессии: бот (bot.py) обновляет, GUI показывает
        self.stats = {
            'sites_cleared': 0, 'kills': 0, 'drones_lost': 0, 'escapes': 0,
        }
        self.last_state = None

        self.client = EveClient(exe_path, state_callback=self.on_new_state)
        # Парсинг выполняется в client.py (_parse_loop) единым парсером EveParserNew
        # (new_parser.py); main.py использует готовый (state, draw_list, payload) из очереди.
        self.bot = EveBot(stats=self.stats)

        # --- NN CONTROL: TCP-серверы состояния и команд (state_server.py) ---
        try:
            self.broadcaster = StateBroadcaster()
            self.client.broadcaster = self.broadcaster
        except Exception as e:
            self.broadcaster = None
            print(f"[App] StateBroadcaster failed: {e}")

        try:
            self.action_receiver = ActionReceiver()
        except Exception as e:
            self.action_receiver = None
            print(f"[App] ActionReceiver failed: {e}")

        self.running = False
        # Тик бота выполняется в отдельном потоке: GUI (кнопка STOP) не
        # блокируется на время действий pyautogui (клики, паузы).
        self._bot_thread = None
        # Флаг от потока хоткея Ctrl+Shift+S -> обрабатывается в logic_loop
        self._hotkey_stop_requested = False

        self.root.protocol("WM_DELETE_WINDOW", self.on_exit)

    def log_safe(self, text):
        if self.root.winfo_exists():
            self.root.after(0, self.update_log_widget, text)

    def update_log_widget(self, text):
        if self.log_view.winfo_exists():
            self.log_view.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {text}\n")
            self.log_view.see(tk.END)

    def build_ui(self):
        # ---- Баннер состояния бота (красный/жёлтый/зелёный/синий) ----
        self.status_banner = tk.Label(self.root, text="SYSTEM STANDBY", bg='#263238', fg='#9aa7ad',
                                      font=("Consolas", 13, "bold"), pady=7, anchor="center")
        self.status_banner.pack(fill=tk.X, padx=10, pady=(10, 0))

        # ---- Статистика сессии (обновляется из self.stats) ----
        self.stats_frame = tk.Frame(self.root, bg='#1a1a1a')
        self.stats_frame.pack(fill=tk.X, padx=10, pady=(6, 0))
        self.stat_vars = {
            'sites':   tk.StringVar(value='0'),
            'kills':   tk.StringVar(value='0'),
            'drones':  tk.StringVar(value='0'),
            'escapes': tk.StringVar(value='0'),
        }
        for key, label in (('sites', 'SITES'), ('kills', 'KILLS'),
                           ('drones', 'DRONES LOST'), ('escapes', 'ESCAPES')):
            cell = tk.Frame(self.stats_frame, bg='#1a1a1a')
            cell.pack(side=tk.LEFT, expand=True, fill=tk.X)
            tk.Label(cell, text=label, bg='#1a1a1a', fg='#666666',
                     font=("Arial", 7, "bold")).pack()
            tk.Label(cell, textvariable=self.stat_vars[key], bg='#1a1a1a', fg='#88ddff',
                     font=("Consolas", 12, "bold")).pack()

        btn_frame = tk.Frame(self.root, bg='#1a1a1a', pady=8)
        btn_frame.pack(fill=tk.X, padx=10)

        self.btn_start = tk.Button(btn_frame, text="START BOT", bg='#1b5e20', fg='white',
                                   command=self.start_bot, height=2, font=("Arial", 10, "bold"))
        self.btn_start.pack(fill=tk.X, pady=2)

        self.btn_stop = tk.Button(btn_frame, text="STOP BOT", bg='#b71c1c', fg='white',
                                  command=self.stop_bot, height=2, state="disabled",
                                  font=("Arial", 10, "bold"))
        self.btn_stop.pack(fill=tk.X, pady=2)

        self.btn_parser_debug = tk.Button(btn_frame, text="SHOW PARSER DEBUG", bg='#37474f', fg='white',
                                          command=self.toggle_parser_debug, height=1, font=("Arial", 9, "bold"))
        self.btn_parser_debug.pack(fill=tk.X, pady=2)

        # Кнопка выгрузки последнего сырого JSON-дампа для отладки парсера
        self.btn_dump_json = tk.Button(btn_frame, text="ВЫГРУЗИТЬ СЫРОЙ JSON", bg='#4a3b00', fg='#ffdd88',
                                       command=self.dump_raw_snapshot, height=1, font=("Arial", 9, "bold"))
        self.btn_dump_json.pack(fill=tk.X, pady=2)

        log_btns = tk.Frame(btn_frame, bg='#1a1a1a')
        log_btns.pack(fill=tk.X, pady=2)
        tk.Button(log_btns, text="CLEAR LOG", bg='#333333', fg='#aaaaaa',
                  command=self.clear_log, height=1, font=("Arial", 8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))
        tk.Button(log_btns, text="COPY LOG", bg='#333333', fg='#aaaaaa',
                  command=self.copy_log, height=1, font=("Arial", 8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

        opts_frame = tk.Frame(self.root, bg='#1a1a1a', padx=10, pady=4)
        opts_frame.pack(fill=tk.X)

        self.mode_var = tk.BooleanVar(value=False)
        self.mode_check = tk.Checkbutton(opts_frame, text="Mode: DRONES (On/Off)", variable=self.mode_var,
                                        command=self.on_mode_change,
                                        bg='#1a1a1a', fg='#00ffaa',
                                        selectcolor='black', activebackground='#1a1a1a',
                                        activeforeground='#00ffaa')
        self.mode_check.pack(anchor="w")

        self.verbose_var = tk.BooleanVar(value=False)
        tk.Checkbutton(opts_frame, text="Verbose Log", variable=self.verbose_var, command=self.toggle_verbose,
                       bg='#1a1a1a', fg='#cccccc', selectcolor='black',
                       activebackground='#1a1a1a', activeforeground='#ffffff').pack(anchor="w")
        tk.Checkbutton(opts_frame, text="NN CONTROL (accept commands from :5556)",
                       variable=self.nn_control_var,
                       command=self.on_nn_control_change,
                       bg='#1a1a1a', fg='#ffaa00', selectcolor='black',
                       activebackground='#1a1a1a', activeforeground='#ffaa00').pack(anchor="w")

        tk.Label(self.root, text="WORLD STATUS", bg='#1a1a1a', fg='#888888', font=("Arial", 8, "bold")).pack(anchor="w", padx=10)
        # state="disabled" позволяет копировать, но запрещает ввод
        self.status_view = tk.Text(self.root, height=18, bg='black', fg='#00ccff', font=("Consolas", 10), padx=10, pady=10, relief="flat", state="normal")
        self.status_view.pack(fill=tk.X, padx=10, pady=5)
        # Только для чтения: блокируем ввод, но оставляем выделение/копирование
        self.status_view.bind("<Key>", lambda e: "break")

        tk.Label(self.root, text="CONSOLE LOG", bg='#1a1a1a', fg='#888888', font=("Arial", 8, "bold")).pack(anchor="w", padx=10)
        self.log_view = tk.Text(self.root, bg='black', fg='#777777', font=("Consolas", 9), padx=10, pady=5, relief="flat", state="normal")
        self.log_view.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.log_view.bind("<Key>", lambda e: "break")
        
        tk.Button(self.root, text="EXIT", command=self.on_exit, bg='#333333', fg='white', relief="flat").pack(fill=tk.X, padx=10, pady=10)

    def on_mode_change(self):
        mode = "DRONES" if self.mode_var.get() else "GUNS"
        self.update_log_widget(f"Combat mode changed to: {mode}")

    def toggle_verbose(self):
        """Синхронизация расширенного лога бота и клиента с чекбоксом."""
        on = self.verbose_var.get()
        self.client.verbose = on
        self.bot.verbose = on
        self.update_log_widget(f"Verbose log: {'ON' if on else 'OFF'}")

    def on_nn_control_change(self):
        on = self.nn_control_var.get()
        self.update_log_widget(f"NN CONTROL: {'ON' if on else 'OFF'}")

    def clear_log(self):
        self.log_view.delete("1.0", tk.END)

    def copy_log(self):
        try:
            text = self.log_view.get("1.0", tk.END)
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.update_log_widget("LOG: скопирован в буфер обмена")
        except Exception as e:
            print(f"[App] copy_log error: {e}")

    def dump_raw_snapshot(self):
        """Кнопка 'ВЫГРУЗИТЬ СЫРОЙ JSON': сохраняет последний дамп eveui.exe в файл."""
        try:
            path = self.client.dump_raw_json()
        except RuntimeError as e:
            self.update_log_widget(f"DUMP FAILED: {e}")
            return
        self.update_log_widget(f"RAW JSON -> {path}")
        print(f"[App] raw dump saved: {path}")

    def update_banner(self, state):
        """Цвет+текст баннера: приоритет DANGER > WARP > DOCKED > LOADING > режим боя."""
        mission = ""
        if self.bot._mission != 'IDLE' and self.bot._current_anom:
            mission = f"  |  {self.bot._mission}: {self.bot._current_anom}"

        if state.local_has_hostile or state.local_has_neutral:
            hostile = "HOSTILE" if state.local_has_hostile else "NEUTRAL"
            self.status_banner.config(text=f"DANGER: {hostile} IN LOCAL{mission}",
                                      bg='#8b0000', fg='#ff8888')
        elif state.is_warp:
            self.status_banner.config(text=f"WARP{mission}", bg='#1565c0', fg='#b3e5fc')
        elif state.is_docked:
            self.status_banner.config(text="DOCKED", bg='#37474f', fg='#cfd8dc')
        elif state.is_loading:
            self.status_banner.config(text="LOADING", bg='#263238', fg='#9aa7ad')
        elif state.combat_mode_active in ("COMBAT", "GUNS", "DRONES"):
            self.status_banner.config(text=f"COMBAT: {state.combat_mode_active}{mission}",
                                      bg='#1b5e20', fg='#c8e6c9')
        elif state.combat_mode_active == "SCANNING":
            self.status_banner.config(text=f"SCANNING / FLYING TO SITE{mission}",
                                      bg='#37474f', fg='#80cbc4')
        elif state.combat_mode_active == "SAFETY":
            self.status_banner.config(text="SAFETY MODE", bg='#8b0000', fg='#ff8888')
        else:
            self.status_banner.config(text="IDLE — STANDING BY", bg='#263238', fg='#9aa7ad')

    def update_session_stats(self):
        """Панель статистики: читает self.stats (обновляет бот в bot.py)."""
        self.stat_vars['sites'].set(str(self.stats.get('sites_cleared', 0)))
        self.stat_vars['kills'].set(str(self.stats.get('kills', 0)))
        self.stat_vars['drones'].set(str(self.stats.get('drones_lost', 0)))
        self.stat_vars['escapes'].set(str(self.stats.get('escapes', 0)))

    def on_new_state(self, state):
        """Callback из потока парсинга (client): перекидываем в GUI-поток."""
        try:
            self.root.after(0, self._apply_new_state, state)
        except Exception:
            pass

    def _apply_new_state(self, state):
        self.last_state = state
        if not self.parser_debug_window or not self.parser_debug_window.winfo_exists():
            return
        self.parser_debug_window.set_state(state)

    def toggle_parser_debug(self):
        """Показать/скрыть окно отладки нового парсера."""
        if not self.parser_debug_window or not self.parser_debug_window.winfo_exists():
            return
        if self.parser_debug_window.state() == "normal":
            self.parser_debug_window.withdraw()
            self.btn_parser_debug.config(text="SHOW PARSER DEBUG")
        else:
            self.parser_debug_window.deiconify()
            self.parser_debug_window.lift()
            # Сразу показываем последнее известное состояние, если оно есть
            if self.parser_debug_window._latest_state is not None:
                self.parser_debug_window.render()
            self.btn_parser_debug.config(text="HIDE PARSER DEBUG")

    def start_bot(self):
        self.running = True
        self.bot._stop_requested = False   # сброс запроса STOP (кнопка/hotkey)
        # Сброс боевого пайплайна: settle-пауза варпа/андока и фокус с чистого листа
        self.bot._just_exited_warp = False
        self.bot._combat_start_ts = 0.0
        self.bot._focus_name = None
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.update_log_widget("SYSTEM: BOT STARTED")

    def stop_bot(self):
        self.running = False
        # Кооперативная остановка: текущее действие бота завершится, новых
        # действий не будет (флаг проверяется между фазами think()).
        self.bot.request_stop()
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.update_log_widget("SYSTEM: BOT STOPPED")

    def on_exit(self):
            print("[App] Shutdown sequence started...")
            self.running = False
            
            # Сначала убиваем процесс через клиент
            if getattr(self, 'broadcaster', None): self.broadcaster.stop()
            if getattr(self, 'action_receiver', None): self.action_receiver.stop()
            if hasattr(self, 'client'):
                self.client.stop()
                
            # Возвращаем stderr на место
            sys.stderr = sys.__stderr__
            
            # Закрываем окно
            self.root.destroy()
            
            # Жесткий выход из Python (завершает все фоновые потоки моментально)
            os._exit(0)

    def generate_report(self, state):
        report = []

        # Текущая миссия бота (полёт по аномалиям из probe scanner)
        mission = self.bot._mission
        if mission != 'IDLE':
            anom = self.bot._current_anom or '?'
            report.append(f"MISSION: {mission} -> {anom}")
        elif not self.running:
            report.append("MISSION: BOT STOPPED")
        else:
            report.append("MISSION: STANDING BY")

        safety = "!!! DANGER !!!" if (state.local_has_hostile or state.local_has_neutral) else "LOCAL SAFE"
        report.append(f"DEFENSE: {safety}")
        
        nav = "STATIONARY"
        if state.is_warp: nav = "WARP"
        elif state.is_establishing: nav = "ALIGNING"
        elif state.is_orbiting: nav = "ORBITING"
        report.append(f"NAV: {nav}")
        def _pct(v):
            return f"{v:.1f}" if isinstance(v, (int, float)) else "n/a"
        report.append(f"SHIP: SHLD {_pct(state.shield_pct)}% | ARM {_pct(state.armor_pct)}% | "
                      f"STRUCT {_pct(getattr(state, 'structure_pct', None))}% | CAP {_pct(getattr(state, 'capacitor_pct', None))}%")
        # --- СТАТУС РЕПОК ---
        if state.repairers:
            rep_status = []
            for name, status in state.repairers.items():
                # Упрощаем имя слота для компактности (InFlightLowSlot0 -> Low 0)
                short_name = name.replace("InFlightLowSlot", "Low ")
                color_mark = "●" if status == "ACTIVE" else "○"
                rep_status.append(f"{short_name}:{color_mark}")
            
            report.append(f"REPAIR: {' '.join(rep_status)}")
        else:
            report.append("REPAIR: NOT FOUND")

        # --- СТАТУС ДВИГАТЕЛЯ (afterburner/MWD) ---
        if getattr(state, 'prop_modules', None):
            prop_status = []
            for name, status in state.prop_modules.items():
                # InFlightMediumSlot0 -> Med 0
                short_name = name.replace("InFlightMediumSlot", "Med ")
                short_name = short_name.replace("InFlightLowSlot", "Low ")
                short_name = short_name.replace("InFlightHighSlot", "High ")
                color_mark = "●" if status == "ACTIVE" else "○"
                prop_status.append(f"{short_name}:{color_mark}")
            report.append(f"PROP: {' '.join(prop_status)}")
        else:
            report.append("PROP: NOT FOUND")
        
        #ЛОГИКА ДРОНОВ
        if state.drones_in_space > 0:
            # Если в космосе > 0, выводим их боевой статус
            if state.drones_returning:
                d_status = "RETURNING"
            elif state.drones_fighting:
                d_status = "FIGHTING"
            else:
                d_status = "IDLE"
            
            warn = " [ATTACKED]" if state.drones_under_attack else ""
            report.append(f"DRONES: {state.drones_in_space} in space | {d_status}{warn}")
        
        elif state.drones_in_bay > 0:
            # Если в космосе 0, но в ангаре кто-то есть
            report.append(f"DRONES: {state.drones_in_bay} in bay")
        
        else:
            # Если вообще нигде не нашли
            report.append("DRONES: EMPTY / NOT FOUND")

        report.append(f"TARGETS: {len(state.targets)} locked")
        
        # ОТЧЕТ ПО NPC (добавлена поддержка Battlecruisers)
        if state.npc_all:
            f = len(state.npc_frigate)
            c = len(state.npc_cruiser)
            bc = len(getattr(state, 'npc_battlecruiser', [])) # Используем getattr на случай, если в parser еще не добавлено
            b = len(state.npc_battleship)
            report.append(f"NPC: {len(state.npc_all)} (F:{f} C:{c} BC:{bc} B:{b})")

        active_mods = [name for name, status in state.modules.items() if status == "ACTIVE"]
        if active_mods:
            report.append(f"ACTIVE MODS: {', '.join(active_mods)}")

        return "\n".join(report)

    def logic_loop(self):
        if not self.root.winfo_exists(): return
        try:
            state, _draw_list, payload = self.client.queue.get_nowait()
            # (state, _draw_list, payload) уже готовы — их положил client._parse_loop.
            # payload — JSON-структура для исполнительного бота/нейросети
            # (state_json.py), она же пишется в state_payload.json.

            # Горячая клавиша STOP (Ctrl+Shift+S): флаг ставит поток хоткея,
            # обработка — здесь, в GUI-потоке.
            if self._hotkey_stop_requested:
                self._hotkey_stop_requested = False
                if self.running:
                    self.update_log_widget("SYSTEM: HOTKEY Ctrl+Shift+S -> STOP")
                    self.stop_bot()

            # Обновление текста состояния (всегда редактируемый, ввод блокируется по <Key>)
            self.status_view.delete("1.0", tk.END)
            self.status_view.insert("1.0", self.generate_report(state))
            self.status_view.config(fg="#ff5555" if (state.local_has_hostile or state.local_has_neutral) else "#00ccff")

            # --- NN CONTROL: обработка команд от нейросети (:5556) ---
            if self.running and self.nn_control_var.get() and self.action_receiver is not None:
                nn_action = self.action_receiver.get_latest()
                if nn_action:
                    self._nn_last_action_ts = time.time()
                    self.update_log_widget(f"[NN] action={nn_action.get('action')} "
                                           f"reason={nn_action.get('reason', '')}")
                    try:
                        self.bot.apply_action(nn_action)
                    except Exception as e:
                        print(f"[App] apply_action error: {e}")

                seen = self.action_receiver.last_seen_ts()
                if seen > 0 and time.time() - seen > 5.0:
                    if time.time() - self._nn_warn_ts > 5.0:
                        self._nn_warn_ts = time.time()
                        self.update_log_widget("[NN] нет команд >5 сек (FSM работает)")

            if self.running:
                # Тик бота — в отдельном потоке: GUI и кнопка STOP реагируют
                # мгновенно, даже пока бот кликает/ждёт внутри think().
                self._start_bot_thread(state, use_drones=self.mode_var.get())

            # Баннер состояния + панель статистики (дёшево, каждый тик)
            self.update_banner(state)
            self.update_session_stats()

        except Empty: pass
        except Exception as e:
            if "invalid command" not in str(e) and self.root.winfo_exists():
                print(f"Logic Error: {e}")

        self.root.after(30, self.logic_loop)

    def _start_bot_thread(self, state, use_drones):
        """Запуск одного тика бота в фоновом потоке.

        Одновременно живёт максимум один тик: пока предыдущий выполняет
        действия (клики, паузы), новые состояния пропускаются — бот возьмёт
        свежее состояние из очереди на следующем обороте logic_loop.
        """
        t = self._bot_thread
        if t is not None and t.is_alive():
            return
        self._bot_thread = threading.Thread(
            target=self._bot_tick, args=(state, use_drones), daemon=True)
        self._bot_thread.start()

    def _bot_tick(self, state, use_drones):
        """Один think() бота вне GUI-потока (ошибки не роняют GUI)."""
        try:
            self.bot.think(state, use_drones=use_drones)
        except Exception as e:
            print(f"[App] Bot error: {e}")

    def _hotkey_loop(self):
        """Глобальный хоткей STOP: Ctrl+Shift+S.

        Работает при любом фокусе (включая окно EVE) — опрос GetAsyncKeyState
        в отдельном потоке с детектом фронтa нажатия. Флаг обрабатывается
        в logic_loop (GUI-поток), чтобы не трогать tkinter из другого потока.
        """
        user32 = ctypes.windll.user32
        VK_CONTROL, VK_SHIFT, VK_S = 0x11, 0x10, 0x53
        print("[App] Горячая клавиша STOP: Ctrl+Shift+S")
        was_down = False
        while True:
            try:
                down = (user32.GetAsyncKeyState(VK_CONTROL) & 0x8000) and \
                       (user32.GetAsyncKeyState(VK_SHIFT) & 0x8000) and \
                       (user32.GetAsyncKeyState(VK_S) & 0x8000)
            except Exception:
                return
            if down and not was_down:
                self._hotkey_stop_requested = True
            was_down = down
            time.sleep(0.05)

    def run(self):
        threading.Thread(target=self.client.reader_loop, daemon=True).start()
        # Поток глобального хоткея STOP (Ctrl+Shift+S)
        threading.Thread(target=self._hotkey_loop, daemon=True).start()
        self.logic_loop()
        self.root.mainloop()

if __name__ == "__main__":
    app = EveApp("eveui.exe")
    app.run()