import re

class WorldState:
    def __init__(self):
        self.shield_pct = 100.0
        self.armor_pct = 100.0
        self.is_warp = False
        self.is_establishing = False
        self.hostiles_in_local = False
        self.local_has_neutral = False
        self.modules = {}
        self.targets = {}
        self.drones_in_space = 0
        self.drones_idle = False
        self.drones_fighting = False
        self.drones_under_attack = False

        # Списки для целей из овервью
        self.npc_all = []
        self.npc_frigate = []
        self.npc_battleship = []
        self.npc_cruiser = []

class EveParser:
    def __init__(self):
        self.cleaner = re.compile("<.*?>")
        # Твой расширенный список
        self.neutral_hints = [
            'pilot has neutral standing', 
            'pilot has no standing', 
            'pilot has bad standing',
            'pilot has terrible standing'
        ]
        self.danger_tags = ["hostile", "terrible", "bad"] # Для подстраховки
        self._drone_statuses = []
        self.min_label_area = 150 * 150

    def clean_html(self, text):
        if not text: return ""
        return re.sub(self.cleaner, "", str(text)).strip()

    def get_val(self, val):
        if isinstance(val, dict): return val.get("int_low32", 0)
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
        if not isinstance(node, dict): return

        ei = node.get('dictEntriesOfInterest', {})
        obj_type = node.get('pythonObjectTypeName', '')
        name = str(ei.get('_name', ''))
        txt = self.clean_html(ei.get('_text') or ei.get('_setText'))

        lx, ly = self.get_val(ei.get('_displayX')), self.get_val(ei.get('_displayY'))
        w, h = self.get_val(ei.get('_displayWidth')), self.get_val(ei.get('_displayHeight'))
        ax, ay = px + lx, py + ly

        # 2. ВЫЗОВ НОВОЙ ПРОВЕРКИ OVERVIEW
        self._check_overview(node, ei, obj_type, state, draw_list, ax, ay, w, h)

        # Вызовы узкоспециализированных проверок
        self._check_navigation(txt, state)
        self._check_drones(node, ei, name, txt, state)
        self._check_ship(node, ei, obj_type, name, state, draw_list, ax, ay, w, h)
        self._check_local(ei, obj_type, state) # Безопасность
        self._check_targets(node, obj_type, state, draw_list, ax, ay, w, h) # Бой
        self._check_frames(obj_type, name, w, h, draw_list, ax, ay) # Дебаг окон

        for c in node.get("children", []) or ei.get("children", []):
            self._walk(c, ax, ay, state, draw_list)



    def _check_overview(self, node, ei, obj_type, state, draw_list, ax, ay, w, h):
        """
        Воссоздаем логику: если текущий узел - SpaceObjectIcon, 
        проверяем его содержимое на наличие 'npc'
        """
        if obj_type == 'SpaceObjectIcon':
            # Ищем 'npc' внутри данных текущего узла (ei) 
            # или заглядываем в его детей (как твой findall_kv)
            found_npc = False
            classification = "NPC"
            
            # Проверяем все записи в текущем словаре интереса
            # (Эквивалент проверки всех key/value в найденном elem_dict)
            node_data_to_check = []
            node_data_to_check.append(ei)
            
            # Добавляем данные детей, так как в твоем коде 'npc' сидел глубже на 4 уровня
            for child in node.get('children', []):
                node_data_to_check.append(child.get('dictEntriesOfInterest', {}))
                # Можно копнуть глубже, если npc сидит еще дальше
                for sub_child in child.get('children', []):
                    node_data_to_check.append(sub_child.get('dictEntriesOfInterest', {}))

            for data_block in node_data_to_check:
                for val in data_block.values():
                    v_str = str(val).lower()
                    if 'npc' in v_str:
                        found_npc = True
                        # Классификация
                        if 'npcfrigate' in v_str: classification = "FRIGATE"
                        elif 'npcbattleship' in v_str: classification = "BATTLESHIP"
                        elif 'npccruiser' in v_str: classification = "CRUISER"
                        break
                if found_npc: break

            if found_npc:
                coords = (ax + w/2, ay + h/2)
                state.npc_all.append(coords)
                
                # Сохраняем в специфические списки
                if classification == "FRIGATE": state.npc_frigate.append(coords)
                elif classification == "BATTLESHIP": state.npc_battleship.append(coords)
                elif classification == "CRUISER": state.npc_cruiser.append(coords)

                draw_list.append({
                    "rect": (ax, ay, w, h),
                    "color": "#ff3333",
                    "priority": 15,
                    "label": classification
                })



    def _check_local(self, ei, obj_type, state):
        """Проверка безопасности в локале"""
        if obj_type == "FlagIconWithState":
            hint = ei.get("_hint")
            if hint:
                # Тотальная очистка для надежного сравнения
                h = str(hint).lower().strip().rstrip('.')
                if any(n in h for n in self.neutral_hints):
                    state.local_has_neutral = True
                    state.hostiles_in_local = True

    def _check_targets(self, node, obj_type, state, draw_list, ax, ay, w, h):
        """Проверка захваченных целей"""
        if obj_type in ("TargetInBar", "TargetInActive"):
            state.targets[id(node)] = {"name": "Target"}
            draw_list.append({
                "rect": (ax, ay, w, h),
                "color": "#ffffff",
                "priority": 10,
                "label": "TARGET"
            })

    def _check_navigation(self, txt, state):
        if "Establishing Warp Vector" in txt: state.is_establishing = True
        elif "Warp Drive Active" in txt: state.is_warp = True

    def _check_drones(self, node, ei, name, txt, state):
        if "Drones in Space" in txt:
            nums = re.findall(r"\d+", txt)
            if nums: state.drones_in_space = int(nums[0])
        elif "Idle" in txt: self._drone_statuses.append("Idle")
        elif "Fighting" in txt: self._drone_statuses.append("Fighting")
        elif "droneGaugeBarDmg" in name:
            if self.get_val(ei.get("_displayWidth")) > 2: state.drones_under_attack = True

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
            if "shieldGauge" in name: state.shield_pct = val
            elif "armorGauge" in name: state.armor_pct = val

    def _check_frames(self, obj_type, name, w, h, draw_list, ax, ay):
        area = w * h
        if area < self.min_label_area or obj_type in ("Root", "Container", "Transform"):
            return
        
        label = name if name else obj_type
        priority = 5 if name else 3
        
        lname = label.lower()
        if "overview" in lname: priority = 10
        elif "local" in lname: priority = 9

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