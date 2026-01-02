class EveBot:
    def __init__(self):
        self.current_state = None
        self.config_mode = "drones"  # Выбор пользователя: "guns" или "drones"

    def set_config_mode(self, mode):
        """Вызывается из GUI при нажатии кнопок"""
        if mode in ["guns", "drones"]:
            self.config_mode = mode

    def think(self, state):
        self.current_state = state

        # 1. ПРИОРИТЕТ: БЕЗОПАСНОСТЬ
        if state.hostiles_in_local or state.local_has_neutral:
            state.combat_mode_active = "SAFETY"
            self.emergency_exit("Враг в локале!")
            return

        # 2. ПРИОРИТЕТ: ВЫЖИВАНИЕ
        if state.shield_pct < 40:
            state.combat_mode_active = "SAFETY"
            self.emergency_exit(f"Критический щит: {state.shield_pct}%")
            return

        # 3. БОЕВАЯ ЛОГИКА
        if state.targets:
            # Используем match-case для выбора метода боя
            match self.config_mode:
                case "guns":
                    state.combat_mode_active = "GUNS"
                    self._logic_guns()
                case "drones":
                    state.combat_mode_active = "DRONES"
                    self._logic_drones()
        else:
            state.combat_mode_active = "SCANNING"
            self.pve_scan_logic()

    def _logic_guns(self):
        """Логика управления пушками"""
        state = self.current_state
        for module_name, status in state.modules.items():
            # Здесь можно добавить фильтр, чтобы не включать МВД или Репку как пушку
            if status == "READY":
                print(f"[Bot] FIRE GUNS: {module_name}")

    def _logic_drones(self):
        """Логика управления дронами"""
        state = self.current_state
        if state.drones_in_space < 5: # Если не все дроны выпущены
             print("[Bot] LAUNCH DRONES")
        
        if state.drones_idle:
             print("[Bot] DRONES ATTACK (Press F)")

    def pve_scan_logic(self):
        """Поиск целей"""
        pass

    def emergency_exit(self, reason):
        print(f"[!!!] EMERGENCY: {reason}")