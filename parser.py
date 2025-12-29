import re


class WorldState:
    def __init__(self):
        self.shield_pct = 100.0
        self.armor_pct = 100.0
        self.is_warp = False
        self.is_establishing = False
        self.hostiles_in_local = False
        self.modules = {}
        self.targets = {}
        self.drones_in_space = 0
        self.drones_idle = False
        self.drones_fighting = False
        self.drones_under_attack = False
        self.local_has_neutral = False
        self.bookmarks = ''


class EveParser:
    def __init__(self):
        self.cleaner = re.compile("<.*?>")
        self.danger_tags = ["neutral", "hostile", "terrible", "bad"]
        self._drone_statuses = []
        self.min_label_area = 150 * 150
        self.neutral_hints = [
            'pilot has neutral standing', 
            'pilot has no standing', 
            'pilot has bad standing',
            'pilot has terrible standing'
]



    def clean_html(self, text):
        if not text:
            return ""
        return re.sub(self.cleaner, "", str(text)).strip()

    def get_val(self, val):
        if isinstance(val, dict):
            return val.get("int_low32", 0)
        return val if isinstance(val, (int, float)) else 0

    def parse(self, root_node):
        state = WorldState()
        draw_list = []
        self._drone_statuses.clear()

        if root_node:
            self._walk(root_node, 0, 0, state, draw_list)

        self._finalize_state(state)
        return state, draw_list

    def _walk(self, node, px, py, state, draw_list):
        if not isinstance(node, dict):
            return

        ei = node.get("dictEntriesOfInterest", {})
        obj_type = node.get("pythonObjectTypeName", "")
        name = str(ei.get("_name", ""))
        txt = self.clean_html(ei.get("_text") or ei.get("_setText"))

        lx = self.get_val(ei.get("_displayX"))
        ly = self.get_val(ei.get("_displayY"))
        w = self.get_val(ei.get("_displayWidth"))
        h = self.get_val(ei.get("_displayHeight"))
        ax, ay = px + lx, py + ly

        self._check_navigation(txt, state)
        self._check_drones(node, ei, name, txt, state)
        self._check_ship(node, ei, obj_type, name, state, draw_list, ax, ay, w, h)
        self._check_local_targets(node, ei, obj_type, state, draw_list, ax, ay, w, h)
        self._check_frames(obj_type, name, w, h, draw_list, ax, ay)

        for c in node.get("children", []) or ei.get("children", []):
            self._walk(c, ax, ay, state, draw_list)

    def _check_navigation(self, txt, state):
        if "Establishing Warp Vector" in txt:
            state.is_establishing = True
        elif "Warp Drive Active" in txt:
            state.is_warp = True

    def _check_drones(self, node, ei, name, txt, state):
        if "Drones in Space" in txt:
            nums = re.findall(r"\d+", txt)
            if nums:
                state.drones_in_space = int(nums[0])

        elif "Idle" in txt:
            self._drone_statuses.append("Idle")
        elif "Fighting" in txt:
            self._drone_statuses.append("Fighting")

        elif "droneGaugeBarDmg" in name:
            if self.get_val(ei.get("_displayWidth")) > 2:
                state.drones_under_attack = True

    def _check_ship(self, node, ei, obj_type, name, state, draw_list, ax, ay, w, h):
        if obj_type == "ShipSlot":
            active = False
            for c in node.get("children", []):
                if "ramp_active" in str(c.get("dictEntriesOfInterest", {})):
                    active = True

            state.modules[name] = "ACTIVE" if active else "READY"
            draw_list.append({
                "rect": (ax, ay, w, h),
                "color": "#00ff00" if active else "#ffaa00",
                "label": name,
                "priority": 9
            })

        elif obj_type == "ShipHudSpriteGauge":
            val = ei.get("_lastValue", 1.0) * 100
            if "shieldGauge" in name:
                state.shield_pct = val
            elif "armorGauge" in name:
                state.armor_pct = val

    def _check_local_targets(self, node, ei, obj_type, state, draw_list, ax, ay, w, h):
        if obj_type == "FlagIconWithState":
            hint = ei.get("_hint")
            if hint:
                # Очищаем: в нижний регистр, убираем точки на концах и лишние пробелы
                hint_clean = str(hint).lower().strip().rstrip('.')
                
                # Проверяем вхождение
                if any(n in hint_clean for n in self.neutral_hints):
                    state.local_has_neutral = True
                    state.hostiles_in_local = True # Чтобы main.py покрасил экран красным

            # --- Цели без изменений ---
            elif obj_type in ("TargetInBar", "TargetInActive"):
                state.targets[id(node)] = {"name": "Target"}
                draw_list.append({
                    "rect": (ax, ay, w, h),
                    "color": "#ffffff",
                    "priority": 10
                })


    def _check_frames(self, obj_type, name, w, h, draw_list, ax, ay):
        area = w * h
        if area < self.min_label_area:
            return

        # --- Фильтр мусора ---
        if obj_type in ("Root", "Container", "Transform"):
            return

        # --- Определение подписи ---
        label = None
        priority = 1

        # 1. По имени (самое информативное)
        if name:
            label = name
            priority = 5

        # 2. По типу
        elif obj_type:
            label = obj_type
            priority = 3

        # 3. Fallback
        else:
            label = f"UI {int(w)}x{int(h)}"

        # --- Специальные повышения приоритета ---
        lname = label.lower()
        if "overview" in lname:
            priority = 10
        elif "local" in lname:
            priority = 9
        elif "drone" in lname:
            priority = 8
        elif "ship" in lname or "hud" in lname:
            priority = 7

        draw_list.append({
            "rect": (ax, ay, w, h),
            "color": "#1f3d5a",
            "label": label,
            "priority": priority
        })


    def _finalize_state(self, state):
        if self._drone_statuses:
            state.drones_idle = any(s == "Idle" for s in self._drone_statuses)
            state.drones_fighting = all(s == "Fighting" for s in self._drone_statuses)

