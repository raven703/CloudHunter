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
from parser import EveParser
from bot import EveBot

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

        self.stderr_redirector = WidgetLogger(self.log_safe)
        sys.stderr = self.stderr_redirector

        self.client = EveClient(exe_path)
        self.parser = EveParser()
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
        report.append(f"SHIP: SHLD {state.shield_pct:.1f}% | ARM {state.armor_pct:.1f}%")
        
        if state.drones_in_space > 0:
            d_status = "IDLE" if state.drones_idle else "FIGHTING"
            warn = " [UNDER ATTACK]" if state.drones_under_attack else ""
            report.append(f"DRONES: {state.drones_in_space} in space | {d_status}{warn}")
        else:
            report.append("DRONES: In Bay")

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
            data = self.client.queue.get_nowait()
            state, draw_list = self.parser.parse(data)
            
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