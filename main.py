import tkinter as tk
from tkinter import ttk
import threading
import time
from queue import Empty
import win32gui
import win32process
import win32api
import win32con
import sys
import os


# Твои рабочие модули
from client import EveClient
from bot import EveBot
from new_parser import format_new_state_report, STANDING_COLORS, STANDING_UNKNOWN, build_debug_blocks

class WidgetLogger:
    def __init__(self, callback):
        self.callback = callback

    def write(self, string):
        if string.strip():
            self.callback(string.strip())

    def flush(self):
        pass

class DebugWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("EVE DEBUG HUD")
        self.geometry("800x800+0+0")
        self.configure(bg='#0a0a0a')
        self.canvas = tk.Canvas(self, bg='#0a0a0a', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        self.withdraw()

class ComparisonWindow(tk.Toplevel):
    """Окно сравнения СТАРОГО и НОВОГО парсеров (пилоты локального чата)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("PARSER COMPARISON")
        self.geometry("560x720+20+20")
        self.configure(bg='#0d0d0d')

        frame = tk.Frame(self, bg='#0d0d0d')
        frame.pack(fill=tk.BOTH, expand=True)

        self.text = tk.Text(frame, bg='#0d0d0d', fg='#dddddd', font=("Consolas", 10),
                            wrap='word', padx=12, pady=12, relief='flat', state='disabled')
        scroll = tk.Scrollbar(frame, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.pack(fill=tk.BOTH, expand=True)

        # Цветовые теги для оформления
        self.text.tag_configure('title', foreground='#ffcc55', font=("Consolas", 11, "bold"))
        self.text.tag_configure('header', foreground='#88ddff', font=("Consolas", 10, "bold"))
        self.text.tag_configure('note', foreground='#ff8888')
        self.text.tag_configure('ok', foreground='#88ff88')
        self.text.tag_configure('dim', foreground='#999999')
        for st, color in STANDING_COLORS.items():
            self.text.tag_configure(st, foreground=color)

        # Закрытие прячет окно, а не убивает
        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        self.withdraw()

    def update_comparison(self, comparison):
        """Полное переписывание содержимого окна данными сравнения."""
        if not self.winfo_exists():
            return

        self.text.config(state="normal")
        self.text.delete("1.0", tk.END)

        ts = comparison.get('timestamp', time.time())
        self.text.insert(tk.END,
            f"=== PARSER COMPARISON @ {time.strftime('%H:%M:%S', time.localtime(ts))} ===\n",
            'title')

        old = comparison.get('old', {})
        new = comparison.get('new', {})

        # --- СТАРЫЙ парсер (то, на чём работает бот) ---
        self.text.insert(tk.END, "\n[OLD parser - bot data]\n", 'header')
        self.text.insert(tk.END,
            f"  pilots listed : {old.get('player_count', 0)}\n"
            f"  neutral flag  : {old.get('has_neutral')}\n"
            f"  hostile flag  : {old.get('has_hostile')}\n",
            'dim')
        if old.get('error'):
            self.text.insert(tk.END, f"  ERROR: {old['error']}\n", 'note')

        # --- НОВЫЙ парсер (правильный разбор локала) ---
        self.text.insert(tk.END, "\n[NEW parser - comparison]\n", 'header')
        self.text.insert(tk.END,
            f"  pilots found  : {new.get('player_count', 0)}\n"
            f"  neutral       : {new.get('has_neutral')}\n"
            f"  hostile       : {new.get('has_hostile')}\n",
            'dim')
        if new.get('error'):
            self.text.insert(tk.END, f"  ERROR: {new['error']}\n", 'note')

        match = comparison.get('match', True)
        self.text.insert(tk.END, f"\nMATCH: {'YES' if match else 'NO'}\n",
                         'ok' if match else 'note')

        notes = comparison.get('notes') or []
        if notes:
            self.text.insert(tk.END, "\nNOTES:\n", 'header')
            for n in notes:
                self.text.insert(tk.END, f"  - {n}\n", 'dim' if match else 'note')

        # --- Все пилоты, найденные НОВЫМ парсером (цвет по статусу) ---
        self.text.insert(tk.END, "\nNEW PARSER PILOTS (full list)\n", 'header')
        players = new.get('players') or []
        if not players:
            self.text.insert(tk.END, "  (no pilots detected)\n", 'dim')

        for p in players:
            st = p.get('standing') or STANDING_UNKNOWN
            tag = st if st in STANDING_COLORS else STANDING_UNKNOWN
            self.text.insert(tk.END, f"  [{st:<11}] ", 'dim')
            self.text.insert(tk.END, p.get('name', ''), tag)
            if p.get('alliance'):
                self.text.insert(tk.END, "  (alliance)", 'dim')
            self.text.insert(tk.END, "\n")

        self.text.see("1.0")
        self.text.config(state="disabled")


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
        self.title("NEW PARSER DEBUG")
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
        
        win_w, win_h = 400, 850
        screen_w = self.root.winfo_screenwidth()
        self.root.geometry(f"{win_w}x{win_h}+{screen_w - win_w}+{10}")
        self.root.configure(bg='#1a1a1a')
        
        self.build_ui()
        self.debug_window = DebugWindow(self.root)
        self.comparison_window = ComparisonWindow(self.root)
        self.parser_debug_window = ParserDebugWindow(self.root)

        self.stderr_redirector = WidgetLogger(self.log_safe)
        sys.stderr = self.stderr_redirector

        self.client = EveClient(exe_path, comparison_callback=self.on_comparison,
                                state_callback=self.on_new_state)
        # Парсинг выполняется в client.py (_parse_loop) старым EveParser;
        # main.py использует уже готовый (state, draw_list) из очереди.
        self.bot = EveBot()
        
        self.running = False
        self.game_hwnd = None
        self.last_game_width = 1024
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_exit)

    def log_safe(self, text):
        if self.root.winfo_exists():
            self.root.after(0, self.update_log_widget, text)

    def update_log_widget(self, text):
        if self.log_view.winfo_exists():
            # Разрешаем редактирование, чтобы вставить текст, затем запрещаем
            self.log_view.config(state="normal")
            self.log_view.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {text}\n")
            self.log_view.see(tk.END)
            self.log_view.config(state="disabled")

    def find_game_window(self):
        target_process = "exefile.exe"
        self.found_hwnd = None
        def enum_windows_callback(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title.startswith("EVE"):
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    try:
                        handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)
                        exe_name = win32process.GetModuleFileNameEx(handle, 0)
                        win32api.CloseHandle(handle)
                        if target_process in exe_name.lower():
                            rect = win32gui.GetWindowRect(hwnd)
                            if rect[2] - rect[0] > 100:
                                self.found_hwnd = hwnd
                                return False
                    except: pass
            return True
        win32gui.EnumWindows(enum_windows_callback, None)
        return self.found_hwnd

    def get_actual_game_width(self):
        if not self.game_hwnd or not win32gui.IsWindow(self.game_hwnd):
            self.game_hwnd = self.find_game_window()
        if self.game_hwnd:
            rect = win32gui.GetClientRect(self.game_hwnd)
            w = rect[2] - rect[0]
            if w > 100: self.last_game_width = w
        return self.last_game_width

    def build_ui(self):
        btn_frame = tk.Frame(self.root, bg='#1a1a1a', pady=10)
        btn_frame.pack(fill=tk.X, padx=10)
        
        self.btn_start = tk.Button(btn_frame, text="START BOT", bg='#1b5e20', fg='white', command=self.start_bot, height=2, font=("Arial", 10, "bold"))
        self.btn_start.pack(fill=tk.X, pady=2)
        
        self.btn_stop = tk.Button(btn_frame, text="STOP BOT", bg='#b71c1c', fg='white', command=self.stop_bot, height=2, state="disabled", font=("Arial", 10, "bold"))
        self.btn_stop.pack(fill=tk.X, pady=2)

        self.btn_comparison = tk.Button(btn_frame, text="SHOW PARSER COMPARISON", bg='#37474f', fg='white',
                                        command=self.toggle_comparison, height=1, font=("Arial", 9, "bold"))
        self.btn_comparison.pack(fill=tk.X, pady=2)

        self.btn_parser_debug = tk.Button(btn_frame, text="SHOW NEW PARSER DEBUG", bg='#37474f', fg='white',
                                          command=self.toggle_parser_debug, height=1, font=("Arial", 9, "bold"))
        self.btn_parser_debug.pack(fill=tk.X, pady=2)

        opts_frame = tk.Frame(self.root, bg='#1a1a1a', padx=10, pady=5)
        opts_frame.pack(fill=tk.X)
        
        self.mode_var = tk.BooleanVar(value=False)
        self.mode_check = tk.Checkbutton(opts_frame, text="Mode: DRONES (On/Off)", variable=self.mode_var,
                                        command=self.on_mode_change,
                                        bg='#1a1a1a', fg='#00ffaa',
                                        selectcolor='black', activebackground='#1a1a1a',
                                        activeforeground='#00ffaa')
        self.mode_check.pack(anchor="w")
        
        self.debug_var = tk.BooleanVar(value=False)
        tk.Checkbutton(opts_frame, text="Show Debug HUD", variable=self.debug_var, command=self.toggle_debug, bg='#1a1a1a', fg='white', selectcolor='black', activebackground='#1a1a1a').pack(anchor="w")

        tk.Label(self.root, text="WORLD STATUS", bg='#1a1a1a', fg='#888888', font=("Arial", 8, "bold")).pack(anchor="w", padx=10)
        # state="disabled" позволяет копировать, но запрещает ввод
        self.status_view = tk.Text(self.root, height=18, bg='black', fg='#00ccff', font=("Consolas", 10), padx=10, pady=10, relief="flat", state="disabled")
        self.status_view.pack(fill=tk.X, padx=10, pady=5)

        tk.Label(self.root, text="CONSOLE LOG", bg='#1a1a1a', fg='#888888', font=("Arial", 8, "bold")).pack(anchor="w", padx=10)
        self.log_view = tk.Text(self.root, bg='black', fg='#777777', font=("Consolas", 9), padx=10, pady=5, relief="flat", state="disabled")
        self.log_view.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        tk.Button(self.root, text="EXIT", command=self.on_exit, bg='#333333', fg='white', relief="flat").pack(fill=tk.X, padx=10, pady=10)

    def on_mode_change(self):
        mode = "DRONES" if self.mode_var.get() else "GUNS"
        self.update_log_widget(f"Combat mode changed to: {mode}")

    def toggle_debug(self):
        if self.debug_var.get(): self.debug_window.deiconify()
        else: self.debug_window.withdraw()

    def on_comparison(self, comparison):
        """Callback из потока парсинга (client): перекидываем в GUI-поток."""
        try:
            self.root.after(0, self._apply_comparison, comparison)
        except Exception:
            pass

    def _apply_comparison(self, comparison):
        if not self.comparison_window or not self.comparison_window.winfo_exists():
            return
        self.comparison_window.update_comparison(comparison)

    def toggle_comparison(self):
        """Показать/скрыть окно сравнения парсеров."""
        if not self.comparison_window or not self.comparison_window.winfo_exists():
            return
        if self.comparison_window.state() == "normal":
            self.comparison_window.withdraw()
            self.btn_comparison.config(text="SHOW PARSER COMPARISON")
        else:
            self.comparison_window.deiconify()
            self.comparison_window.lift()
            self.btn_comparison.config(text="HIDE PARSER COMPARISON")

    def on_new_state(self, state):
        """Callback из потока парсинга (client): перекидываем в GUI-поток."""
        try:
            self.root.after(0, self._apply_new_state, state)
        except Exception:
            pass

    def _apply_new_state(self, state):
        if not self.parser_debug_window or not self.parser_debug_window.winfo_exists():
            return
        self.parser_debug_window.set_state(state)

    def toggle_parser_debug(self):
        """Показать/скрыть окно отладки нового парсера."""
        if not self.parser_debug_window or not self.parser_debug_window.winfo_exists():
            return
        if self.parser_debug_window.state() == "normal":
            self.parser_debug_window.withdraw()
            self.btn_parser_debug.config(text="SHOW NEW PARSER DEBUG")
        else:
            self.parser_debug_window.deiconify()
            self.parser_debug_window.lift()
            # Сразу показываем последнее известное состояние, если оно есть
            if self.parser_debug_window._latest_state is not None:
                self.parser_debug_window.render()
            self.btn_parser_debug.config(text="HIDE NEW PARSER DEBUG")

    def start_bot(self):
        self.running = True
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.update_log_widget("SYSTEM: BOT STARTED")

    def stop_bot(self):
        self.running = False
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.update_log_widget("SYSTEM: BOT STOPPED")

    def on_exit(self):
            print("[App] Shutdown sequence started...")
            self.running = False
            
            # Сначала убиваем процесс через клиент
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
        safety = "!!! DANGER !!!" if (state.hostiles_in_local or state.local_has_neutral) else "LOCAL SAFE"
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
            state, draw_list = self.client.queue.get_nowait()
            # (state, draw_list) уже готовы — их положил client._parse_loop
            
            # Обновление текста состояния (с возможностью выделения)
            self.status_view.config(state="normal")
            self.status_view.delete("1.0", tk.END)
            self.status_view.insert("1.0", self.generate_report(state))
            self.status_view.config(state="disabled")
            
            if state.hostiles_in_local or state.local_has_neutral:
                self.status_view.config(fg="#ff5555")
            else:
                self.status_view.config(fg="#00ccff")

            if self.running:
                self.bot.think(state, use_drones=self.mode_var.get())

            if self.debug_var.get():
                self.render_debug(draw_list)

        except Empty: pass
        except Exception as e:
            if "invalid command" not in str(e) and self.root.winfo_exists():
                print(f"Logic Error: {e}")

        self.root.after(30, self.logic_loop)

    def render_debug(self, draw_list):
        if not self.debug_window.winfo_exists(): return
        canvas = self.debug_window.canvas
        canvas.delete("all")
        game_w = self.get_actual_game_width()
        canvas_w = canvas.winfo_width()
        scale = canvas_w / game_w if game_w > 0 else 1.0

        for item in sorted(draw_list, key=lambda x: x.get("priority", 0)):
            ax, ay, w, h = item["rect"]
            x1, y1, x2, y2 = ax*scale, ay*scale, (ax+w)*scale, (ay+h)*scale
            canvas.create_rectangle(x1, y1, x2, y2, outline=item["color"], width=(2 if item.get("priority", 0) > 5 else 1))
            if item.get("label"):
                canvas.create_text(x1+4, y1+2, text=item["label"], fill=item["color"], anchor="nw", font=("Consolas", 8))

    def run(self):
        threading.Thread(target=self.client.reader_loop, daemon=True).start()
        self.logic_loop()
        self.root.mainloop()

if __name__ == "__main__":
    app = EveApp("eveui.exe")
    app.run()