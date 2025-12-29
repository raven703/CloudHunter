import tkinter as tk
from threading import Thread
from queue import Empty
from client import EveClient
from parser import EveParser
from brain import EveBrain

class EveApp:
    def __init__(self, exe_path):
        self.client = EveClient(exe_path)
        self.parser = EveParser()
        self.brain = EveBrain()
        
        self.root = tk.Tk()
        self.root.title("EVE Automation - WorldState Mode")
        self.root.geometry("1100x850")
        self.root.configure(bg='#0a0a0a')

        self.canvas = tk.Canvas(self.root, bg='#0a0a0a', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        self.status_label = tk.Label(self.root, text="Waiting for data...", fg="#00ccff", bg="#111111", 
                                    font=("Consolas", 10), justify=tk.LEFT, anchor="w", padx=15, pady=10)
        self.status_label.pack(fill=tk.X, side=tk.BOTTOM)
        self.scale = 0.6

    def generate_report(self, state):
        """Формирует отчет на основе объекта WorldState"""
        report = []
        
        # --- 1. Навигация ---
        if state.is_establishing:
            nav_status = "ESTABLISHING WARP VECTOR"
        elif state.is_warp:
            nav_status = "WARP DRIVE ACTIVE"
        else:
            nav_status = "STATIONARY"
            
        # --- 2. Безопасность ---
        safety = "!!! DANGER !!!" if state.hostiles_in_local else "SAFE"
        report.append(f"STATUS: {safety} | {nav_status}")
        
        # --- 3. Состояние корабля ---
        report.append(f"SHIP: SHIELD {state.shield_pct:.1f}% | ARMOR {state.armor_pct:.1f}%")
        
        # --- 4. Дроны (Новое!) ---
        if state.drones_in_space > 0:
            d_status = "IDLE" if state.drones_idle else "FIGHTING" if state.drones_fighting else "MIXED"
            attack_warn = " [UNDER ATTACK]" if state.drones_under_attack else ""
            report.append(f"DRONES: {state.drones_in_space} in space | {d_status}{attack_warn}")
        else:
            report.append("DRONES: No drones in space")
        
        # --- 5. Цели ---
        if state.targets:
            report.append(f"\n--- TARGETS ({len(state.targets)}) ---")
            for addr, info in state.targets.items():
                report.append(f" > {info['name']} (UID: {str(addr)[-5:]})")
        
        # --- 6. Модули ---
        if state.modules:
            report.append("\n--- MODULES ---")
            for m, s in sorted(state.modules.items()):
                icon = "[FIRE]" if s == "ACTIVE" else "[ . ]"
                report.append(f"{icon} {m}: {s}")
                
        return "\n".join(report)

    def update_loop(self):
        try:
            # Получаем сырой JSON из очереди клиента
            data = self.client.queue.get_nowait()
            
            # 1. Парсинг (вызываем метод parse, который возвращает state и список для отрисовки)
            world_state, draw_list = self.parser.parse(data)
            
            # 2. Логика (Brain) - теперь передаем весь объект состояния
            self.brain.think(world_state)
            
            # 3. Отрисовка UI
            self.canvas.delete("all")
            for item in draw_list:
                ax, ay, w, h = item['rect']
                x1, y1 = ax * self.scale, ay * self.scale
                x2, y2 = (ax + w) * self.scale, (ay + h) * self.scale
                
                self.canvas.create_rectangle(x1, y1, x2, y2, outline=item['color'])
                if item['text']:
                    self.canvas.create_text(x1, y1 - 2, text=item['text'], fill=item['color'], 
                                            font=("Arial", 8), anchor="sw")

            # 4. Обновление текстовой диагностики
            self.status_label.config(text=self.generate_report(world_state))
            
            # Логика смены цветов фона
            if world_state.hostiles_in_local:
                self.status_label.config(bg="#440000", fg="#ff5555") # Тревожный красный
            elif world_state.is_establishing or world_state.is_warp:
                self.status_label.config(bg="#002233", fg="#00ccff") # Синий (в полете)
            else:
                self.status_label.config(bg="#111111", fg="#00ccff") # Обычный черный

        except Empty:
            pass
        except Exception as e:
            print(f"Update Error: {e}")
            
        self.root.after(30, self.update_loop)

    def run(self):
        # Запуск потока чтения памяти
        Thread(target=self.client.reader_loop, daemon=True).start()
        self.update_loop()
        self.root.mainloop()

if __name__ == "__main__":
    # Укажи путь к своему exe или эмулятору
    app = EveApp("eveui.exe")
    app.run()