import json
import subprocess
import threading
import tkinter as tk
from queue import Queue, Empty
import re

class EveRealTimeUI:
    def __init__(self, exe_path):
        self.exe_path = exe_path
        self.queue = Queue(maxsize=2)
        self.running = True
        
        self.root = tk.Tk()
        self.root.title("EVE Online Radar: Modules & Targets")
        self.root.geometry("1100x850")
        self.root.configure(bg='#0a0a0a')

        self.canvas = tk.Canvas(self.root, bg='#0a0a0a', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        self.status_frame = tk.Frame(self.root, bg='#111111', bd=2, relief=tk.RIDGE)
        self.status_frame.pack(fill=tk.X, side=tk.BOTTOM)
        
        self.status_label = tk.Label(
            self.status_frame, 
            text="Ожидание данных...", 
            fg="#00ccff", bg="#111111", 
            font=("Consolas", 10), justify=tk.LEFT, anchor="w",
            padx=15, pady=10
        )
        self.status_label.pack(side=tk.LEFT, fill=tk.X)
        
        self.scale = 0.6
        self.current_modules = {}
        self.current_targets = []
        self.is_locked = False # Глобальный статус лока

    def get_val(self, val):
        if isinstance(val, dict): return val.get('int_low32', 0)
        return val if isinstance(val, (int, float)) else 0

    def clean_html(self, raw_html):
        """Очистка имен целей от тегов <color> и т.д."""
        cleanr = re.compile('<.*?>')
        cleantext = re.sub(cleanr, '', str(raw_html))
        return cleantext

    def draw_recursive(self, node, px, py):
        if not isinstance(node, dict): return

        ei = node.get('dictEntriesOfInterest', {})
        children = node.get('children') or ei.get('children')

        lx = self.get_val(ei.get('_displayX', 0))
        ly = self.get_val(ei.get('_displayY', 0))
        w = self.get_val(ei.get('_displayWidth', 0))
        h = self.get_val(ei.get('_displayHeight', 0))
        
        ax, ay = px + lx, py + ly
        obj_type = node.get('pythonObjectTypeName', '')
        name = ei.get('_name', '')

        if w > 1 and h > 1:
            x1, y1 = ax * self.scale, ay * self.scale
            x2, y2 = (ax + w) * self.scale, (ay + h) * self.scale
            
            color = "#1f3d5a"
            is_important = False
            label_text = name

            # --- 1. ЛОГИКА МОДУЛЕЙ ---
            if obj_type == "ShipSlot":
                is_important = True
                active = False
                if isinstance(children, list):
                    for child in children:
                        if 'ramp_active' in child.get('dictEntriesOfInterest', {}):
                            active = child['dictEntriesOfInterest']['ramp_active']
                            break
                self.current_modules[name] = "ACTIVE" if active else "READY"
                color = "#00ff00" if active else "#ffcc00"

            # --- 2. ЛОГИКА ЦЕЛЕЙ (На основе вашего старого кода) ---
            # Ищем TargetInBar или TargetInActive
            elif obj_type in ["TargetInBar", "TargetInActive"]:
                self.is_locked = True
                is_important = True
                color = "#ffffff"
                
                # Пытаемся найти имя цели внутри текста меток
                target_name = "Target"
                if isinstance(children, list):
                    for child in children:
                        c_ei = child.get('dictEntriesOfInterest', {})
                        # Ищем любое текстовое поле, которое не пустое
                        text_val = c_ei.get('_text') or c_ei.get('_name')
                        if text_val and text_val not in ["target", "TargetInBar"]:
                            target_name = self.clean_html(text_val)
                            break
                
                label_text = target_name
                if target_name not in self.current_targets:
                    self.current_targets.append(target_name)

            # Отрисовка
            if is_important or w > 20: # Рисуем важные или крупные объекты
                self.canvas.create_rectangle(x1, y1, x2, y2, outline=color, width=1)
                if is_important:
                    self.canvas.create_text(x1, y1-2, text=label_text, fill=color, 
                                           font=("Arial", 8), anchor="sw")

        if isinstance(children, list):
            for child in children:
                self.draw_recursive(child, ax, ay)

    def update_gui(self):
        try:
            data = self.queue.get_nowait()
            self.canvas.delete("all")
            
            self.current_modules = {}
            self.current_targets = []
            self.is_locked = False
            
            self.draw_recursive(data, 0, 0)
            
            report = []
            # Блок целей
            if self.is_locked:
                report.append("!!! TARGET LOCKED !!!")
                for t in self.current_targets:
                    report.append(f" > {t}")
                report.append("")
            else:
                report.append("[ No Targets ]\n")
            
            # Блок модулей
            if self.current_modules:
                report.append("--- MODULES ---")
                for m in sorted(self.current_modules.keys()):
                    status = self.current_modules[m]
                    icon = "[ FIRE ]" if status == "ACTIVE" else "[  .   ]"
                    report.append(f"{icon} {m.ljust(18)} : {status}")
            
            self.status_label.config(text="\n".join(report))
            # Красим текст в красный если есть цель
            self.status_label.config(fg="#ff5555" if self.is_locked else "#00ccff")

        except Empty:
            pass

        if self.running:
            self.root.after(30, self.update_gui)

    def reader_loop(self):
        # Добавляем -remove-other-dict-entries для скорости
        process = subprocess.Popen(
            [self.exe_path, "-remove-other-dict-entries"], 
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace', bufsize=10**7
        )
        while self.running:
            line = process.stdout.readline()
            if not line: break
            if line.strip().startswith("{"):
                try:
                    data = json.loads(line)
                    if self.queue.full(): self.queue.get_nowait()
                    self.queue.put(data)
                except: continue

    def start(self):
        threading.Thread(target=self.reader_loop, daemon=True).start()
        self.update_gui()
        self.root.mainloop()
        self.running = False

if __name__ == "__main__":
    app = EveRealTimeUI("eveui.exe")
    app.start()