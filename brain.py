import time
# import pyautogui # Раскомментируйте, когда установите библиотеку

class EveBrain:
    def __init__(self):
        self.last_action_time = 0
        self.action_cooldown = 2.0 

    def execute_logic(self, modules, targets):
        current_time = time.time()
        
        # Если есть цели и мы не на КД
        if targets and (current_time - self.last_action_time > self.action_cooldown):
            # Проверяем первый слот
            if modules.get('inFlightHighSlot1') == "READY":
                print(f"[Brain] Вижу {len(targets)} целей. Огонь!")
                # pyautogui.press('f1') 
                self.last_action_time = current_time