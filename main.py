import json
import subprocess
import threading
import tkinter as tk
from queue import Queue, Empty
import re
import sys

class EveRealTimeUI:
    def __init__(self, exe_path):
        self.exe_path = exe_path
        self.queue = Queue(maxsize=2)
        self.running = True
        
        self.root = tk.Tk()
        self.root.title("EVE Radar: Targets & Click Points")
        self.root.geometry("1100x850")
        self.root.configure(bg='#0a0a0a')

        self.canvas = tk.Canvas(self.root, bg='#0a0a0a', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        self.status_frame = tk.Frame(self.root, bg='#111111', bd=2, relief=tk.RIDGE)
        self.status_frame.pack(fill=tk.X, side=tk.BOTTOM)
        
        self.status_label = tk.Label(
            self.status_frame, 
            text="Initializing...", 
            fg="#00ccff", bg="#111111", 
            font=("Consolas", 10), justify=tk.LEFT, anchor="w",
            padx=15, pady=10
        )
        self.status_label.pack(side=tk.LEFT, fill=tk.X)
        
        self.scale = 0.6
        self.current_modules = {}
        # Словарь объектов целей: {address: {"name": str, "x": int, "y": int}}
        self.current_targets = {}
        self.is_locked = False

    def clean_html(self, raw_html):
        cleanr = re.compile('<.*?>')
        cleantext = re.sub(cleanr, '', str(raw_html))
        return cleantext.strip()

    def find_target_name(self, node):
        if not isinstance(node, dict): return None
        ent = node.get('dictEntriesOfInterest', {})
        text = ent.get('_text')
        
        if text and len(str(text)) > 1 and "barandimagecont" not in str(text):
            cleaned = self.clean_html(text)
            if cleaned: return cleaned
            
        children = node.get('children') or ent.get('children')
        if isinstance(children, list):
            for child in children:
                res = self.find_target_name(child)
                if res: return res
        return None

    def draw_recursive(self, node, px, py):
        if not isinstance(node, dict): return

        ei = node.get('dictEntriesOfInterest', {})
        children = node.get('children') or ei.get('children')

        def v(val):
            if isinstance(val, dict): return val.get('int_low32', 0)
            return val if isinstance(val, (int, float)) else 0

        lx, ly = v(ei.get('_displayX')), v(ei.get('_displayY'))
        w, h = v(ei.get('_displayWidth')), v(ei.get('_displayHeight'))
        
        # Абсолютные координаты в окне игры
        ax, ay = px + lx, py + ly
        obj_type = node.get('pythonObjectTypeName', '')
        obj_addr = node.get('pythonObjectAddress', id(node))
        name = ei.get('_name', '')

        if w > 1 and h > 1:
            x1, y1 = ax * self.scale, ay * self.scale
            x2, y2 = (ax + w) * self.scale, (ay + h) * self.scale
            
            color = "#1f3d5a"
            is_important = False
            label_text = name

            # --- МОДУЛИ ---
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

            # --- ЦЕЛИ ---
            elif obj_type in ["TargetInBar", "TargetInActive"]:
                self.is_locked = True
                is_important = True
                color = "#ffffff"
                
                t_name = self.find_target_name(node) or "Target"
                
                # Рассчитываем центр контейнера для клика (в игровых координатах)
                center_x = ax + (w / 2)
                center_y = ay + (h / 2)
                
                self.current_targets[obj_addr] = {
                    "name": t_name,
                    "x": int(center_x),
                    "y": int(center_y)
                }
                label_text = f"{t_name} ({int(center_x)},{int(center_y)})"

            # Отрисовка на радаре
            if is_important or (w > 15 and h > 15):
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
            self.current_targets = {} 
            self.is_locked = False
            
            self.draw_recursive(data, 0, 0)
            
            report = []
            if self.is_locked:
                report.append(f"!!! TARGETS LOCKED: {len(self.current_targets)} !!!")
                for addr, info in self.current_targets.items():
                    # Выводим имя и координаты центра для каждой цели
                    report.append(f" > {info['name'].ljust(15)} @ [X:{info['x']} Y:{info['y']}]")
                report.append("")
                # Диагностика в консоль (можно использовать для автокликера)
                # print(f"Targets: {self.current_targets}")
            else:
                report.append("[ No Targets Locked ]\n")
            
            if self.current_modules:
                report.append("--- MODULES ---")
                for m in sorted(self.current_modules.keys()):
                    status = self.current_modules[m]
                    icon = "[ FIRE ]" if status == "ACTIVE" else "[  .   ]"
                    report.append(f"{icon} {m.ljust(18)} : {status}")
            
            self.status_label.config(text="\n".join(report))
            self.status_label.config(fg="#ff5555" if self.is_locked else "#00ccff")

        except Empty:
            pass

        if self.running:
            self.root.after(30, self.update_gui)

    def reader_loop(self):
        print(f"[Python] Connecting to {self.exe_path}...")
        process = subprocess.Popen(
            [self.exe_path, "-remove-other-dict-entries"], 
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace', bufsize=10**7
        )

        def log_reader(pipe):
            for line in pipe:
                sys.stderr.write(f"  [Go-Log]: {line}")
                sys.stderr.flush()
        
        threading.Thread(target=log_reader, args=(process.stderr,), daemon=True).start()

        while self.running:
            line = process.stdout.readline()
            if not line: break
            line_stripped = line.strip()
            if not line_stripped.startswith("{"): continue
            try:
                data = json.loads(line_stripped)
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