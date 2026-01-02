import re
from dataclasses import dataclass, field
from typing import List, Dict

@dataclass
class WorldState:
    shield_pct: float = 100.0
    armor_pct: float = 100.0
    # Для изменяемых типов (dict, list) в dataclass используется default_factory
    modules: dict = field(default_factory=dict)
    targets: Dict[int, dict] = field(default_factory=dict)
    repairers: dict = field(default_factory=dict)

    is_warp: bool = False
    is_establishing: bool = False
    is_orbiting: bool = False
    hostiles_in_local: bool = False
    local_has_neutral: bool = False
    
    combat_mode_active: str = "IDLE"  # Текущий активный режим: GUNS, DRONES, SAFETY, IDLE
    
    drones_in_space: int = 0
    drones_in_bay: int = 0
    drones_idle: bool = False
    drones_fighting: bool = False
    drones_under_attack: bool = False
    drones_returning: bool = False

    # Списки NPC
    npc_all: List[tuple] = field(default_factory=list)
    npc_frigate: List[tuple] = field(default_factory=list)
    npc_battleship: List[tuple] = field(default_factory=list)
    npc_cruiser: List[tuple] = field(default_factory=list)
    npc_battlecruiser: List[tuple] = field(default_factory=list)

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
                            elif 'npcbattlecruiser' in v_str: 
                                ship_type = "BATTLECRUISER"    
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
                    elif ship_type == "BATTLECRUISER": 
                        state.npc_battlecruiser.append(coords)
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
        elif "Orbiting" in txt: state.is_orbiting = True

    def _check_drones(self, node, ei, name, txt, state):
        if "Drones in Space" in txt:
            nums = re.findall(r"\d+", txt)
            if nums: state.drones_in_space = int(nums[0])
        elif "Drones in Bay" in txt:
            nums = re.findall(r"\d+", txt)
            if nums: state.drones_in_bay = int(nums[0])
        
        # Собираем статусы для финализации
        if "Idle" in txt: self._drone_statuses.append("Idle")
        elif "Fighting" in txt: self._drone_statuses.append("Fighting")
        elif "Returning" in txt: self._drone_statuses.append("Returning")
        
        if "droneGaugeBarDmg" in name:
            if self.get_val(ei.get("_displayWidth")) > 2: 
                state.drones_under_attack = True

    def _check_armor_repairer(self, node):
        """Прямая проверка текстуры иконки внутри ModuleButton"""
        rep_texture = "res:/ui/texture/icons/1_64_11.png"
        
        # Проходим по детям ShipSlot (ищем ModuleButton)
        for child_button in node.get("children", []) or []:
            # Проходим по детям ModuleButton (ищем Icon)
            for sub_child in child_button.get("children", []) or []:
                sei = sub_child.get('dictEntriesOfInterest', {})
                if str(sei.get('_texturePath')) == rep_texture:
                    return True
        return False
        
        # Рекурсия по детям
        for c in node.get("children", []) or []:
            if self._check_armor_repairer(c):
                print(f"DEBUG: Found target {val} in field {field}") # Раскомментируй для проверки в консоли
                return True
            else:
                print(f"no repair")
        return False

    def _check_ship(self, node, ei, obj_type, name, state, draw_list, ax, ay, w, h):
        if obj_type == "ShipSlot":
            active = False
            # Проверяем на репку по твоей структуре
            is_repairer = self._check_armor_repairer(node)
            
            # Ищем ModuleButton среди детей, чтобы проверить активацию
            for child in node.get("children", []) or []:
                cei = child.get("dictEntriesOfInterest", {})
                
                # 1. Проверка через ramp_active (как в твоем дампе)
                if cei.get("ramp_active") is True:
                    active = True
                    break
                
                # 2. Твоя старая проверка по имени/ширине для подстраховки
                c_name = str(cei.get('_name', ''))
                if "ramp_active" in c_name and self.get_val(cei.get('_displayWidth')) > 0:
                    active = True
                    break

            # Фиксируем состояние в WorldState
            status = "ACTIVE" if active else "READY"
            state.modules[name] = status
            if is_repairer:
                state.repairers[name] = status

            # Цвет для Debug HUD: Розовый для репки, Зеленый для активных, Оранжевый для готовых
            color = "#ff00ff" if is_repairer else ("#00ff00" if active else "#ffaa00")
            draw_list.append({
                "rect": (ax, ay, w, h),
                "color": color,
                "label": f"REP:{name[-1]}" if is_repairer else name,
                "priority": 10
            })

        elif obj_type == "ShipHudSpriteGauge":
            # Твоя логика HP остается без изменений
            raw_val = ei.get("_lastValue", 1.0)
            val = self.get_val(raw_val)
            percent = float(val) * 100
            if "shieldGauge" in name: state.shield_pct = percent
            elif "armorGauge" in name: state.armor_pct = percent

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
            s_set = set(self._drone_statuses)
            
            # Строгий приоритет статусов
            if "Returning" in s_set:
                state.drones_returning = True
                state.drones_fighting = False
                state.drones_idle = False
            elif "Fighting" in s_set:
                state.drones_returning = False
                state.drones_fighting = True
                state.drones_idle = False
            else:
                state.drones_returning = False
                state.drones_fighting = False
                state.drones_idle = True
        else:
            state.drones_returning = False
            state.drones_fighting = False
            state.drones_idle = False