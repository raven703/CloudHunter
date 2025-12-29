import re

class WorldState:
    def __init__(self):
        self.shield_pct = 100.0
        self.armor_pct = 100.0
        self.is_warp = False
        self.is_establishing = False
        self.hostiles_in_local = False
        self.bookmarks = {}
        self.modules = {}
        self.targets = {}
        self.drones_in_space = 0
        self.drones_idle = False
        self.drones_fighting = False
        self.drones_under_attack = False

class EveParser:
    def __init__(self):
        self.cleaner = re.compile('<.*?>')
        self.danger_tags = ['neutral', 'hostile', 'terrible', 'bad']

    def clean_html(self, text):
        if not text: return ""
        return re.sub(self.cleaner, '', str(text)).strip()

    def get_val(self, val):
        if isinstance(val, dict): return val.get('int_low32', 0)
        return val if isinstance(val, (int, float)) else 0

    def parse(self, root_node):
        state = WorldState()
        draw_list = []
        self._drone_statuses = [] # Временный список для сбора статусов дронов

        if not root_node: return state, draw_list

        # Запускаем один проход по дереву, который заполнит всё
        self._recursive_walk(root_node, 0, 0, state, draw_list)

        # После обхода финализируем статус дронов
        if self._drone_statuses:
            state.drones_idle = any(s == 'Idle' for s in self._drone_statuses)
            state.drones_fighting = all(s == 'Fighting' for s in self._drone_statuses)

        return state, draw_list

    def _recursive_walk(self, node, px, py, state, draw_list):
        if not isinstance(node, dict): return
        
        ei = node.get('dictEntriesOfInterest', {})
        obj_type = node.get('pythonObjectTypeName', '')
        name = str(ei.get('_name', ''))
        
        # Получаем и чистим текст сразу
        raw_text = ei.get('_text') or ei.get('_setText')
        txt = self.clean_html(raw_text)
        
        # Координаты
        lx, ly = self.get_val(ei.get('_displayX')), self.get_val(ei.get('_displayY'))
        w, h = self.get_val(ei.get('_displayWidth')), self.get_val(ei.get('_displayHeight'))
        ax, ay = px + lx, py + ly

        # --- 1. ПРОВЕРКА ВАРПА (Ищем текст в любой метке) ---
        if 'Establishing Warp Vector' in txt:
            state.is_establishing = True
        elif 'Warp Drive Active' in txt:
            state.is_warp = True

        # --- 2. МОДУЛИ ---
        if obj_type == "ShipSlot":
            self._handle_modules(node, ei, name, state, draw_list, ax, ay, w, h)
        
        # --- 3. ДРОНЫ ---
        elif "Drones in Space" in txt:
            nums = re.findall(r'\d+', txt)
            if nums: state.drones_in_space = int(nums[0])
            
        elif name == "entryLabel" or obj_type == "EveLabelMedium":
            if "Idle" in txt: self._drone_statuses.append("Idle")
            elif "Fighting" in txt: self._drone_statuses.append("Fighting")
            
        elif "droneGaugeBarDmg" in name:
            if w > 2: state.drones_under_attack = True

        # --- 4. ЩИТЫ / БРОНЯ ---
        elif obj_type == "ShipHudSpriteGauge":
            val = ei.get('_lastValue', 1.0) * 100
            if "shieldGauge" in name: state.shield_pct = val
            elif "armorGauge" in name: state.armor_pct = val

        # --- 5. ЛОКАЛ (Безопасность) ---
        elif obj_type == "FlagIconWithState":
            hint = str(ei.get('_hint', '')).lower()
            if any(tag in hint for tag in self.danger_tags):
                state.hostiles_in_local = True

        # --- 6. ЦЕЛИ ---
        elif obj_type in ["TargetInBar", "TargetInActive"]:
            state.targets[id(node)] = {"name": "Target", "x": int(ax + w/2), "y": int(ay + h/2)}
            draw_list.append({'rect': (ax, ay, w, h), 'color': "#ffffff", 'text': None})

        # --- 7. РАМКИ ОКОН ---
        elif w > 70 and h > 70 and obj_type not in ["ShipSlot", "Root"]:
            draw_list.append({'rect': (ax, ay, w, h), 'color': "#1f3d5a", 'text': None})

        # Рекурсия (идем вглубь)
        children = node.get('children') or ei.get('children')
        if isinstance(children, list):
            for child in children:
                self._recursive_walk(child, ax, ay, state, draw_list)

    def _handle_modules(self, node, ei, name, state, draw_list, ax, ay, w, h):
        is_active = False
        for c in (node.get('children') or ei.get('children') or []):
            if 'ramp_active' in str(c.get('dictEntriesOfInterest', {})):
                is_active = c['dictEntriesOfInterest'].get('ramp_active', False)
        state.modules[name] = "ACTIVE" if is_active else "READY"
        draw_list.append({'rect': (ax, ay, w, h), 'color': "#00ff00" if is_active else "#ffcc00", 'text': name})