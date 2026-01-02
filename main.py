import win32gui
import win32process
import win32api
import win32con
import tkinter as tk
from threading import Thread
from queue import Empty
from client import EveClient
from parser import EveParser
from bot import EveBot 


class EveApp:
    def __init__(self, exe_path):
        self.client = EveClient(exe_path)
        self.parser = EveParser()
        self.bot = EveBot()

        self.root = tk.Tk()
        self.root.title("EVE Automation - WorldState Mode")
        # 1. Задаем размеры окна
        win_w = 800
        win_h = 800
        # 2. Получаем разрешение ТВОЕГО монитора (рабочего стола)
        screen_w = self.root.winfo_screenwidth()
        # 3. Вычисляем X так, чтобы правый край окна совпал с правым краем экрана
        # Координата X = Ширина экрана - Ширина окна
        pos_x = screen_w - win_w
        pos_y = 0  # Прижать к самому верху
        # 4. Устанавливаем геометрию
        self.root.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
        self.scale = 1.0
        self.root.configure(bg='#0a0a0a')

        # 2. Добавляем переменные для хранения данных об окне игры
        self.game_hwnd = None      # Здесь будет храниться ID окна игры
        self.last_game_width = 1024 # Дефолтная ширина на случай, если игра закрыта
        self.scale = 1.0           # Стартовый масштаб (обновится в первом цикле)

        self.canvas = tk.Canvas(
            self.root,
            bg='#0a0a0a',
            highlightthickness=0,
            #width=1024, # Укажи ширину области игры
            #height=768  # Укажи высоту (оставь место снизу под Label)
            )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.status_label = tk.Label(
            self.root,
            text="Waiting for data...",
            fg="#00ccff",
            bg="#111111",
            font=("Consolas", 10),
            justify=tk.LEFT,
            height=8,
            anchor="nw",
            padx=15,
            pady=10
        )
        self.status_label.pack(fill=tk.X, side=tk.BOTTOM)


    def find_game_window(self):
            """Поиск окна exefile.exe с заголовком EVE"""
            target_process = "exefile.exe"
            self.found_hwnd = None

            def enum_windows_callback(hwnd, _):
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    # Проверяем, что окно начинается на EVE
                    if title.startswith("EVE"):
                        # Получаем PID процесса этого окна
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        try:
                            # Открываем процесс, чтобы узнать его имя
                            handle = win32api.OpenProcess(
                                win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
                                 False, pid)
                            exe_name = win32process.GetModuleFileNameEx(handle, 0)
                            win32api.CloseHandle(handle)
                            
                            if target_process in exe_name.lower():
                                # Добавляем проверку на размер, чтобы не поймать "призраков"
                                rect = win32gui.GetWindowRect(hwnd)
                                if rect[2] - rect[0] > 100 and rect[3] - rect[1] > 100:
                                    self.found_hwnd = hwnd
                                    print(f"DEBUG: Found '{target_process}' | PID: {pid} | Window: '{title}' | HWND: {hwnd}")
                                    return False # Остановить перебор
                        except Exception:
                            pass
                return True

            win32gui.EnumWindows(enum_windows_callback, None)
            return self.found_hwnd

    def get_actual_game_width(self):
        """Берет ширину игрового пространства"""
        # Если окно потеряно или еще не найдено — ищем
        if not self.game_hwnd or not win32gui.IsWindow(self.game_hwnd):
            self.game_hwnd = self.find_game_window()

        if self.game_hwnd:
            rect = win32gui.GetClientRect(self.game_hwnd)
            w = rect[2] - rect[0]
            if w > 100:
                self.last_game_width = w
                return w
        return self.last_game_width        

    def generate_report(self, state):
        report = []

# --- БЕЗОПАСНОСТЬ И НАВИГАЦИЯ ---
        # Было: if-elif. Стало: match-case
        match (state.hostiles_in_local or state.local_has_neutral):
            case True:  safety = "!!! DANGER: HOSTILE/NEUTRAL IN LOCAL !!!"
            case False: safety = "LOCAL SAFE"
        
        # --- 2. Навигация ---
        match True:
            case state.is_establishing:
                nav = "ESTABLISHING WARP VECTOR"
            case state.is_warp:
                nav = "WARP DRIVE ACTIVE"
            case state.is_orbiting:
                nav = "ORBITING"
            case _:
                nav = "STATIONARY"

        report.append(f"STATUS: {safety} | {nav}")
        # --- 3. Корабль ---
        report.append(f"SHIP: SHIELD {state.shield_pct:.1f}% | ARMOR {state.armor_pct:.1f}%")

# --- ДРОНЫ ---
        # Используем сопоставление кортежа (idle, fighting)
        match state.drones_in_space:
            case 0:
                report.append("DRONES: No drones in space")
            case n:
                match (state.drones_idle, state.drones_fighting):
                    case (True, False): d_status = "IDLE"
                    case (False, True): d_status = "FIGHTING"
                    case _:             d_status = "MIXED"
                warn = " [UNDER ATTACK]" if state.drones_under_attack else ""
                report.append(f"DRONES: {n} | {d_status}{warn}")

        # --- 5. Цели ---
        if state.targets:
            report.append(f"\n--- TARGETS ({len(state.targets)}) ---")
            # Мы теперь используем id(node) как ключ, выведем просто количество или часть ID
            for t_id in state.targets:
                report.append(f" > Target Locked (ID: {str(t_id)[-5:]})")

        # --- 6. Модули ---
        if state.modules:
            report.append("\n--- MODULES ---")
            for m, s in sorted(state.modules.items()):
                icon = "[FIRE]" if s == "ACTIVE" else "[ . ]"
                report.append(f"{icon} {m}: {s}")
        
        if state.npc_all:
            report.append(f"\n--- OVERVIEW NPC ({len(state.npc_all)}) ---")
            npc_types = [("Frigates", state.npc_frigate), 
                         ("Cruisers", state.npc_cruiser), 
                         ("Battleships", state.npc_battleship)]
            for name, collection in npc_types:
                if collection:
                    report.append(f" > {name}: {len(collection)}")
            

        return "\n".join(report)

    def update_loop(self):
        try:
            data = self.client.queue.get_nowait()
            
                # --- РАСЧЕТ АВТОМАТИЧЕСКОГО МАСШТАБА ---
            game_w = self.get_actual_game_width()
            canvas_w = self.canvas.winfo_width()
            
            # Обновляем self.scale перед парсингом/отрисовкой
            if canvas_w > 1 and game_w > 1:
                self.scale = canvas_w / game_w
            # ---------------------------------------

            world_state, draw_list = self.parser.parse(data)
            self.bot.think(world_state)

            self.canvas.delete("all")
            occupied_labels = []

            def intersects(x, y):
                for ox, oy in occupied_labels:
                    if abs(x - ox) < 80 and abs(y - oy) < 15: # Немного увеличил отступ
                        return True
                return False

            # Сортируем по приоритету, чтобы важные рамки (цели) были сверху
            for item in sorted(draw_list, key=lambda x: x.get("priority", 0)):
                ax, ay, w, h = item["rect"]
                x1, y1 = ax * self.scale, ay * self.scale
                x2, y2 = (ax + w) * self.scale, (ay + h) * self.scale

                self.canvas.create_rectangle(
                    x1, y1, x2, y2, outline=item["color"], width=(2 if item.get("priority", 0) > 5 else 1)
                )

                label = item.get("label")
                if label:
                    tx, ty = x1 + 4, y1 + 4
                    if not intersects(tx, ty):
                        self.canvas.create_text(
                            tx, ty,
                            text=label,
                            fill="#7fdfff" if item["color"] != "#ffffff" else "#ffffff",
                            font=("Consolas", 9, "bold"),
                            anchor="nw"
                        )
                        occupied_labels.append((tx, ty))

            # Обновление текста
            self.status_label.config(text=self.generate_report(world_state))

            # Цвет фона (Красный если опасность)
            if world_state.hostiles_in_local or world_state.local_has_neutral:
                self.status_label.config(bg="#440000", fg="#ff5555")
            elif world_state.is_establishing or world_state.is_warp:
                self.status_label.config(bg="#002233", fg="#00ccff")
            else:
                self.status_label.config(bg="#111111", fg="#00ccff")

        except Empty:
            pass
        except Exception as e:
            print("Update error:", e)

        self.root.after(30, self.update_loop)

    def run(self):
        Thread(target=self.client.reader_loop, daemon=True).start()
        self.update_loop()
        self.root.mainloop()

if __name__ == "__main__":
    app = EveApp("eveui.exe")
    app.run()