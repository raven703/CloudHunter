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
            if obj_type == 'SpaceObjectIcon':
                found_npc = False
                ship_type = "NPC"
                
                # Сканируем данные узла и его прямых детей на наличие 'npc'
                data_to_scan = [ei]
                for child in node.get('children', []) or []:
                    data_to_scan.append(child.get('dictEntriesOfInterest', {}))

                for data_block in data_to_scan:
                    for val in data_block.values():
                        v_str = str(val).lower()
                        if 'npc' in v_str:
                            found_npc = True
                            if 'npcfrigate' in v_str: 
                                ship_type = "FRIGATE"
                            elif 'npcbattleship' in v_str: 
                                ship_type = "BATTLESHIP"
                            elif 'npccruiser' in v_str: 
                                ship_type = "CRUISER"
                            break
                    if found_npc: break

                if found_npc:
                    coords = (ax + w/2, ay + h/2)
                    state.npc_all.append(coords)
                    
                    # Наполняем только выбранные списки
                    if ship_type == "FRIGATE": 
                        state.npc_frigate.append(coords)
                    elif ship_type == "BATTLESHIP": 
                        state.npc_battleship.append(coords)
                    elif ship_type == "CRUISER": 
                        state.npc_cruiser.append(coords)

                    draw_list.append({
                        "rect": (ax, ay, w, h),
                        "color": "#ff3333",
                        "priority": 15,
                        "label": ship_type
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
            # Проходим по детям слота
            for c in node.get("children", []) or []:
                child_ei = c.get("dictEntriesOfInterest", {})
                
                # 1. Проверяем по имени анимации активации
                child_name = str(child_ei.get('_name', ''))
                if "ramp_active" in child_name:
                    # Проверяем, что этот объект действительно отображается (ширина > 0)
                    if self.get_val(child_ei.get('_displayWidth')) > 0:
                        active = True
                        break
                
                # 2. Дополнительная проверка по текстуре (на случай, если имя не меняется)
                texture = str(child_ei.get('_texturePath', '')).lower()
                if "glow" in texture or "active" in texture:
                    active = True
                    break

            state.modules[name] = "ACTIVE" if active else "READY"
           # if "MediumSlot" in name:
           #     print(f"Slot: {name}, Children count: {len(node.get('children', []))}")
           #     for c in node.get('children', []):
           #         print(f"  Child: {c.get('dictEntriesOfInterest', {}).get('_name')}")
            
            draw_list.append({
                "rect": (ax, ay, w, h),
                "color": "#00ff00" if active else "#ffaa00",
                "label": name,
                "priority": 9
            })
            
        # Если это полоска жизни (Shield/Armor)
        # Этот блок сработает для других узлов, когда рекурсия дойдет до них
        elif obj_type == "ShipHudSpriteGauge":
            raw_val = ei.get("_lastValue", 1.0)
            val = self.get_val(raw_val)
            
            # ВАЖНО: Добавь этот принт один раз, чтобы увидеть реальные цифры в консоли
            # print(f"DEBUG HP: {name} raw={raw_val} val={val}")
            
            percent = float(val) * 100
            
            if "shieldGauge" in name:
                state.shield_pct = percent
            elif "armorGauge" in name:
                state.armor_pct = percent

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