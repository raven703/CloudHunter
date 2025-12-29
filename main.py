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

        self.status_label = tk.Label(
            self.root,
            text="Waiting for data...",
            fg="#00ccff",
            bg="#111111",
            font=("Consolas", 10),
            justify=tk.LEFT,
            anchor="w",
            padx=15,
            pady=10
        )
        self.status_label.pack(fill=tk.X, side=tk.BOTTOM)

        self.scale = 0.6

    def generate_report(self, state):
        report = []

        # Логика безопасности
        is_danger = state.hostiles_in_local or state.local_has_neutral
        safety = "!!! DANGER !!!" if is_danger else "LOCAL SAFE"
        
        # Навигация
        if state.is_establishing:
            nav = "WARP VECTOR"
        elif state.is_warp:
            nav = "IN WARP"
        else:
            nav = "STATIONARY"

        report.append(f"STATUS: {safety} | {nav}")
        report.append(f"SHIP: SHIELD {state.shield_pct:.1f}% | ARMOR {state.armor_pct:.1f}%")
        
        # Дроны
        if state.drones_in_space > 0:
            d_status = "IDLE" if state.drones_idle else "FIGHTING" if state.drones_fighting else "MIXED"
            report.append(f"DRONES: {state.drones_in_space} | {d_status}")
        else:
            report.append("DRONES: No drones")

        return "\n".join(report)

    def update_loop(self):
        try:
            data = self.client.queue.get_nowait()
            world_state, draw_list = self.parser.parse(data)
            self.brain.think(world_state)

            self.canvas.delete("all")
            occupied_labels = []

            def intersects(x, y):
                for ox, oy in occupied_labels:
                    if abs(x - ox) < 70 and abs(y - oy) < 14:
                        return True
                return False

            for item in sorted(draw_list, key=lambda x: x.get("priority", 0), reverse=True):
                ax, ay, w, h = item["rect"]
                x1, y1 = ax * self.scale, ay * self.scale
                x2, y2 = (ax + w) * self.scale, (ay + h) * self.scale

                self.canvas.create_rectangle(
                    x1, y1, x2, y2, outline=item["color"]
                )

                label = item.get("label")
                if label:
                    tx, ty = x1 + 4, y1 + 4
                    if not intersects(tx, ty):
                        self.canvas.create_text(
                            tx,
                            ty,
                            text=label,
                            fill="#7fdfff",
                            font=("Consolas", 9, "bold"),
                            anchor="nw"
                        )
                        occupied_labels.append((tx, ty))

            self.status_label.config(text=self.generate_report(world_state))

            if world_state.hostiles_in_local:
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
