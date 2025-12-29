class EveBrain:
    def __init__(self):
        self.current_state = None

    def think(self, state):
        """
        Основной цикл принятия решений. 
        Получает объект WorldState и решает, что делать.
        """
        self.current_state = state

        # 1. ПРИОРИТЕТ: БЕЗОПАСНОСТЬ (Safety First)
        if state.hostiles_in_local:
            self.emergency_exit("Враг в локале!")
            return

        # 2. ПРИОРИТЕТ: ВЫЖИВАНИЕ (Ship Status)
        if state.shield_pct < 40:
            self.emergency_exit(f"Критический щит: {state.shield_pct}%")
            return

        # 3. БОЕВАЯ ЛОГИКА (Combat)
        if state.targets:
            self.combat_logic()
        else:
            self.pve_scan_logic()

    def combat_logic(self):
        """Управление модулями в бою"""
        state = self.current_state
        
        # Если есть цели, но модули не активны — включить огонь
        for module_name, status in state.modules.items():
            if status == "READY":
                # Здесь будет вызов команды клика: self.click_module(module_name)
                print(f"[Brain] Активирую модуль: {module_name}")

    def pve_scan_logic(self):
        """Поиск целей, если их сейчас нет"""
        # Если в WorldState пусто, значит нужно искать NPC в овервью
        pass

    def emergency_exit(self, reason):
        """Логика экстренного отварпа"""
        print(f"[!!!] ЭКСТРЕННЫЙ ВЫХОД: {reason}")
        print(f"[Brain] Кликаю отварп на safe1:")
        
        # Ищем координаты сейф-спота, которые сохранил парсер
        #safe_coords = self.current_state.bookmarks.get("safe1")
        #if safe_coords:
        #    x, y = safe_coords
        #    print(f"[Brain] Кликаю отварп на safe1: {x}, {y}")
        #    # self.client.click(x, y)
        #else:
        #    print("[Brain] Ошибка: Координаты safe1 не найдены в закладках!")