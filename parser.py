import re

class WorldState:
    def __init__(self):
        self.shield_pct = 100.0
        self.armor_pct = 100.0
        
        # Навигация (строго по игровым текстам)
        self.is_warp = False              # Warp Drive Active
        self.is_establishing = False      # Establishing Warp Vector
        
        self.hostiles_in_local = False
        self.bookmarks = {}
        self.modules = {}
        self.targets = {}
        self.drones_in_space = 0
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

    def find_nav_text(self, node):
        """Рекурсивный поиск конкретных фраз навигации"""
        if not isinstance(node, dict): return None
        ei = node.get('dictEntriesOfInterest', {})
        txt = self.clean_html(ei.get('_text') or ei.get('_setText'))
        
        if txt == 'Establishing Warp Vector':
            return 'establishing'
        if txt == 'Warp Drive Active':
            return 'warping'
            
        children = node.get('children') or ei.get('children') or []
        for child in children:
            res = self.find_nav_text(child)
            if res: return res
        return None

    def parse(self, root_node):
        state = WorldState()
        draw_list = []
        
        if not root_node: return state, draw_list

        # 1. Поиск статуса (проверяем слои 10-18)
        root_children = root_node.get('children', [])
        for idx in range(min(len(root_children), 10), min(len(root_children), 18)):
            nav_res = self.find_nav_text(root_children[idx])
            if nav_res == 'establishing':
                state.is_establishing = True
                break
            elif nav_res == 'warping':
                state.is_warp = True
                break

        # 2. Основной обход (рамки и данные)
        self.parse_frame(root_node, 0, 0, state, draw_list)
        return state, draw_list

    def parse_frame(self, node, px, py, state, draw_list):
        if not isinstance(node, dict): return
        
        ei = node.get('dictEntriesOfInterest', {})
        lx, ly = self.get_val(ei.get('_displayX')), self.get_val(ei.get('_displayY'))
        w, h = self.get_val(ei.get('_displayWidth')), self.get_val(ei.get('_displayHeight'))
        ax, ay = px + lx, py + ly
        
        obj_type = node.get('pythonObjectTypeName', '')
        name = str(ei.get('_name', ''))

        # Модули
        if obj_type == "ShipSlot":
            is_active = False
            children = node.get('children') or ei.get('children') or []
            for c in children:
                cei = c.get('dictEntriesOfInterest', {})
                if 'ramp_active' in cei:
                    is_active = cei['ramp_active']
            state.modules[name] = "ACTIVE" if is_active else "READY"
            draw_list.append({'rect': (ax, ay, w, h), 'color': "#00ff00" if is_active else "#ffcc00", 'text': name})

        # Цели
        elif obj_type in ["TargetInBar", "TargetInActive"]:
            state.targets[id(node)] = {"name": "Target", "x": int(ax + w/2), "y": int(ay + h/2)}
            draw_list.append({'rect': (ax, ay, w, h), 'color': "#ffffff", 'text': None})

        # Синие рамки окон (чтобы видеть, что парсер жив)
        elif w > 70 and h > 70:
            draw_list.append({'rect': (ax, ay, w, h), 'color': "#1f3d5a", 'text': None})

        # Рекурсия
        children = node.get('children') or ei.get('children')
        if isinstance(children, list):
            for child in children:
                self.parse_frame(child, ax, ay, state, draw_list)