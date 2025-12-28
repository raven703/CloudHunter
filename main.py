import json
import subprocess
import threading
import tkinter as tk
from queue import Queue, Empty

class EveRealTimeUI:
    def __init__(self, exe_path):
        self.exe_path = exe_path
        self.queue = Queue(maxsize=2)  # Храним только самые свежие кадры
        self.running = True
        
        # Настройка окна
        self.root = tk.Tk()
        self.root.title("EVE Online UI Radar (Real-time)")
        
        self.canvas = tk.Canvas(self.root, bg='#0a0a0a', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        self.scale = 0.6  # Масштаб отображения

    def get_val(self, val):
        if isinstance(val, dict): 
            return val.get('int_low32', 0)
        return val if isinstance(val, (int, float)) else 0

    def draw_recursive(self, node, px, py):
        if not isinstance(node, dict): 
            return

        ei = node.get('dictEntriesOfInterest', {})
        children = node.get('children')
        if not isinstance(children, list):
            children = ei.get('children')

        lx = self.get_val(ei.get('_displayX', 0))
        ly = self.get_val(ei.get('_displayY', 0))
        w = self.get_val(ei.get('_displayWidth', 0))
        h = self.get_val(ei.get('_displayHeight', 0))
        
        ax, ay = px + lx, py + ly

        if w > 5 and h > 5:
            x1, y1 = ax * self.scale, ay * self.scale
            x2, y2 = (ax + w) * self.scale, (ay + h) * self.scale
            
            obj_type = node.get('pythonObjectTypeName', '')
            name = ei.get('_name', '')
            
            color = "#1f3d5a"
            is_important = False
            
            if "ShieldGauge" in obj_type or "shield" in name.lower():
                color, is_important = "#ff3300", True
            elif "ShipSlot" in obj_type or "Module" in obj_type:
                color, is_important = "#00ff00", True
            elif "Overview" in obj_type:
                color, is_important = "#ffcc00", True

            self.canvas.create_rectangle(x1, y1, x2, y2, outline=color, width=1)
            
            if is_important:
                self.canvas.create_text(x1, y1-2, text=f"{name or obj_type}", 
                                       fill="white", font=("Arial", 7), anchor="sw")

        if isinstance(children, list):
            for child in children:
                self.draw_recursive(child, ax, ay)

    def reader_loop(self):
        """Фоновый поток чтения данных из Go"""
        print(f"[Python] Запуск {self.exe_path}...")
        
        process = subprocess.Popen(
            [self.exe_path, "-remove-other-dict-entries"], 
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',      # Явно указываем UTF-8
            errors='replace',
            bufsize=10**7 # Большой буфер для JSON
        )

        def log_reader(pipe):
            for line in pipe:
                print(f"  [Go]: {line.strip()}")
        
        threading.Thread(target=log_reader, args=(process.stderr,), daemon=True).start()

        while self.running:
            line = process.stdout.readline()
            if not line:
                break
            
            line = line.strip()
            if not line or not line.startswith("{"):
                continue

            try:
                data = json.loads(line)
                # Оставляем только свежий кадр
                if self.queue.full():
                    try: self.queue.get_nowait()
                    except Empty: pass
                self.queue.put(data)
            except json.JSONDecodeError:
                continue

    def update_gui(self):
        """Цикл отрисовки в основном потоке"""
        try:
            data = self.queue.get_nowait()
            self.canvas.delete("all")
            
            ei = data.get('dictEntriesOfInterest', {})
            sw = self.get_val(ei.get('_displayWidth', 1572))
            sh = self.get_val(ei.get('_displayHeight', 909))
            self.canvas.config(width=sw * self.scale, height=sh * self.scale)

            self.draw_recursive(data, 0, 0)
        except Empty:
            pass

        if self.running:
            self.root.after(30, self.update_gui)

    def start(self):
        """Запуск приложения"""
        t = threading.Thread(target=self.reader_loop, daemon=True)
        t.start()
        
        self.update_gui()
        self.root.mainloop()
        self.running = False

if __name__ == "__main__":
    app = EveRealTimeUI("eveui.exe")
    app.start()