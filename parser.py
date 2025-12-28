import re

class EveParser:
    def __init__(self):
        self.cleaner = re.compile('<.*?>')

    def clean_html(self, text):
        return re.sub(self.cleaner, '', str(text)).strip()

    def get_val(self, val):
        if isinstance(val, dict): return val.get('int_low32', 0)
        return val if isinstance(val, (int, float)) else 0

    def find_text_recursive(self, node):
        if not isinstance(node, dict): return None
        ei = node.get('dictEntriesOfInterest', {})
        text = ei.get('_text')
        if text and len(str(text)) > 1 and "barandimagecont" not in str(text):
            return self.clean_html(text)
        children = node.get('children') or ei.get('children')
        if isinstance(children, list):
            for child in children:
                res = self.find_text_recursive(child)
                if res: return res
        return None

    def parse_frame(self, node, px, py, results):
        if not isinstance(node, dict): return
        ei = node.get('dictEntriesOfInterest', {})
        lx, ly = self.get_val(ei.get('_displayX')), self.get_val(ei.get('_displayY'))
        w, h = self.get_val(ei.get('_displayWidth')), self.get_val(ei.get('_displayHeight'))
        ax, ay = px + lx, py + ly
        obj_type = node.get('pythonObjectTypeName', '')
        obj_addr = node.get('pythonObjectAddress', id(node))
        name = ei.get('_name', '')

        if obj_type == "ShipSlot":
            active = False
            children = node.get('children') or ei.get('children')
            if isinstance(children, list):
                for c in children:
                    if 'ramp_active' in c.get('dictEntriesOfInterest', {}):
                        active = c['dictEntriesOfInterest']['ramp_active']
                        break
            results['modules'][name] = "ACTIVE" if active else "READY"
            results['draw_list'].append({'rect': (ax, ay, w, h), 'color': "#00ff00" if active else "#ffcc00", 'text': name})

        elif obj_type in ["TargetInBar", "TargetInActive"]:
            t_name = self.find_text_recursive(node) or "Target"
            cx, cy = ax + (w / 2), ay + (h / 2)
            results['targets'][obj_addr] = {"name": t_name, "x": int(cx), "y": int(cy)}
            results['draw_list'].append({'rect': (ax, ay, w, h), 'color': "#ffffff", 'text': f"{t_name} [{int(cx)},{int(cy)}]"})

        elif w > 20 and h > 20:
             results['draw_list'].append({'rect': (ax, ay, w, h), 'color': "#1f3d5a", 'text': None})

        children = node.get('children') or ei.get('children')
        if isinstance(children, list):
            for child in children:
                self.parse_frame(child, ax, ay, results)