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
        
        # 1. Логика навигации (расширенная)
        if state.is_establishing:
            warp_status = "ESTABLISHING WARP VECTOR"
            warp_color = "#ffff00"  # Желтый
        elif state.is_warp:
            warp_status = "WARP DRIVE ACTIVE"
            warp_color = "#00ffff"  # Голубой
        else:
            warp_status = "STATIONARY"
            warp_color = "#ffffff"  # Белый

        # Статус безопасности и корабля
        safety = "!!! DANGER !!!" if state.hostiles_in_local else "SAFE"
        
        # Первая строка отчета
        report.append(f"STATUS: {safety} | {warp_status}")
        report.append(f"SHIP: SHIELD {state.shield_pct:.1f}% | ARMOR {state.armor_pct:.1f}%")
        report.append(f"DRONES: {state.drones_in_space} in space" + (" [UNDER ATTACK]" if state.drones_under_attack else ""))
        
        # Цели
        if state.targets:
            report.append(f"\n--- TARGETS ({len(state.targets)}) ---")
            for addr, info in state.targets.items():
                report.append(f" > {info['name']} at {info['x']},{info['y']}")
        
        # Модули
        if state.modules:
            report.append("\n--- MODULES ---")
            for m, s in sorted(state.modules.items()):
                icon = "[FIRE]" if s == "ACTIVE" else "[ . ]"
                report.append(f"{icon} {m}: {s}")
        
        # Обновляем цвет текста статуса навигации в label (опционально)
        # Если хочешь, чтобы весь текст менял цвет при варпе, раскомментируй:
        # self.status_label.config(fg=warp_color)
                
        return "\n".join(report)
        """Формирует отчет на основе объекта WorldState"""
        report = []
        
        # Статус безопасности и корабля
        safety = "!!! DANGER !!!" if state.hostiles_in_local else "SAFE"
        warp = "WARPING" if state.is_warp else "STATIONARY"
        report.append(f"STATUS: {safety} | {warp}")
        report.append(f"SHIP: SHIELD {state.shield_pct:.1f}% | ARMOR {state.armor_pct:.1f}%")
        report.append(f"DRONES: {state.drones_in_space} in space" + (" [UNDER ATTACK]" if state.drones_under_attack else ""))
        
        # Цели
        if state.targets:
            report.append(f"\n--- TARGETS ({len(state.targets)}) ---")
            for addr, info in state.targets.items():
                report.append(f" > {info['name']} at {info['x']},{info['y']}")
        
        # Модули
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
            
            # Подсветка фона при опасности
            if world_state.hostiles_in_local:
                self.status_label.config(bg="#330000", fg="#ff5555")
            else:
                self.status_label.config(bg="#111111", fg="#00ccff")

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