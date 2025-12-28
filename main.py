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
        self.root.title("EVE Automation Modularity")
        self.root.geometry("1100x850")
        self.root.configure(bg='#0a0a0a')

        self.canvas = tk.Canvas(self.root, bg='#0a0a0a', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        self.status_label = tk.Label(self.root, text="Waiting...", fg="#00ccff", bg="#111111", 
                                    font=("Consolas", 10), justify=tk.LEFT, anchor="w", padx=15, pady=10)
        self.status_label.pack(fill=tk.X, side=tk.BOTTOM)
        self.scale = 0.6

    def generate_report(self, results):
        """Тот самый пропущенный метод для формирования текста"""
        report = []
        if results['targets']:
            report.append(f"!!! TARGETS: {len(results['targets'])} !!!")
            for addr, info in results['targets'].items():
                report.append(f" > {info['name']} @ {info['x']},{info['y']}")
        
        if results['modules']:
            report.append("\n--- MODULES ---")
            for m in sorted(results['modules'].keys()):
                state = results['modules'][m]
                icon = "[FIRE]" if state == "ACTIVE" else "[ . ]"
                report.append(f"{icon} {m}: {state}")
        return "\n".join(report) if report else "No active data"

    def update_loop(self):
        try:
            data = self.client.queue.get_nowait()
            results = {'modules': {}, 'targets': {}, 'draw_list': []}
            
            # 1. Парсинг
            self.parser.parse_frame(data, 0, 0, results)
            
            # 2. Логика (Brain)
            self.brain.execute_logic(results['modules'], results['targets'])
            
            # 3. Отрисовка
            self.canvas.delete("all")
            for item in results['draw_list']:
                ax, ay, w, h = item['rect']
                x1, y1, x2, y2 = ax*self.scale, ay*self.scale, (ax+w)*self.scale, (ay+h)*self.scale
                self.canvas.create_rectangle(x1, y1, x2, y2, outline=item['color'])
                if item['text']:
                    self.canvas.create_text(x1, y1-2, text=item['text'], fill=item['color'], font=("Arial", 8), anchor="sw")

            # 4. Диагностика в окне
            self.status_label.config(text=self.generate_report(results))
            self.status_label.config(fg="#ff5555" if results['targets'] else "#00ccff")

        except Empty:
            pass
        self.root.after(30, self.update_loop)

    def run(self):
        Thread(target=self.client.reader_loop, daemon=True).start()
        self.update_loop()
        self.root.mainloop()

if __name__ == "__main__":
    app = EveApp("eveui.exe")
    app.run()