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

        # --- 1. Безопасность (Объединенная логика) ---
        if state.hostiles_in_local or state.local_has_neutral:
            safety = "!!! DANGER: HOSTILE/NEUTRAL IN LOCAL !!!"
        else:
            safety = "LOCAL SAFE"
        
        # --- 2. Навигация ---
        if state.is_establishing:
            nav = "ESTABLISHING WARP VECTOR"
        elif state.is_warp:
            nav = "WARP DRIVE ACTIVE"
        else:
            nav = "STATIONARY"

        report.append(f"STATUS: {safety} | {nav}")

        # --- 3. Корабль ---
        report.append(f"SHIP: SHIELD {state.shield_pct:.1f}% | ARMOR {state.armor_pct:.1f}%")

        # --- 4. Дроны ---
        if state.drones_in_space > 0:
            d_status = "IDLE" if state.drones_idle else "FIGHTING" if state.drones_fighting else "MIXED"
            warn = " [UNDER ATTACK]" if state.drones_under_attack else ""
            report.append(f"DRONES: {state.drones_in_space} | {d_status}{warn}")
        else:
            report.append("DRONES: No drones in space")

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
            if state.npc_frigate: report.append(f" > Frigates: {len(state.npc_frigate)}")
            if state.npc_cruiser: report.append(f" > Cruisers: {len(state.npc_cruiser)}")
            if state.npc_battleship: report.append(f" > Battleships: {len(state.npc_battleship)}")
            

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