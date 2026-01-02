import pyautogui
import pygetwindow as gw
import time
import random


class EveBot:
    def __init__(self):
        self.current_state = None
        self.use_drones = True
        # Небольшая пауза между действиями для стабильности
        pyautogui.PAUSE = 0.8 
        self.window_name = "EVE - Nikky Naari" # Имя окна (может быть "EVE - Имя Персонажа")
        self.no_targets_timer = None # Таймер для сбора дронов
        # Поправка на заголовок окна (если мажет по вертикали)
        self.CORRECTION_Y = 31
    
    def _get_offset(self):  
        """Получаем экранные координаты окна игры"""
        try:
            windows = gw.getWindowsWithTitle(self.window_name)
            if windows:
                win = windows[0]
                # Прибавляем коррекцию на заголовок окна к Y
                return win.left, win.top + self.CORRECTION_Y
            return 0, 0
        except:
            return 0, 0

    def _activate_game_window(self):
        """Находит окно игры и выводит его на передний план"""
        try:
            # Ищем окна, в названии которых есть "EVE"
            windows = gw.getWindowsWithTitle(self.window_name)
            if windows:
                win = windows[0]
                if not win.isActive:
                    print(f"[Bot] Активирую окно: {win.title}")
                    win.activate()
                    time.sleep(0.5) # Ждем, пока окно "прогрузится" в фокусе
                return True
            else:
                print("[Bot] Ошибка: Окно EVE не найдено!")
                return False
        except Exception as e:
            print(f"[Bot] Ошибка при активации окна: {e}")
            return False


    def think(self, state, use_drones: bool):
        self.current_state = state
        self.use_drones = use_drones

        # 0. Активация окна перед любым вводом
        if not self._activate_game_window():
            print(f"bot: no game window active")
            return # Если окно не найдено, ничего не делаем
        
        # 1. ПРИОРИТЕТ: ВЫЖИВАНИЕ (Репка по горячей клавише)
        self._check_and_repair()

        # 2. ПРИОРИТЕТ: БЕЗОПАСНОСТЬ
        if state.hostiles_in_local or state.local_has_neutral:
            state.combat_mode_active = "SAFETY"
            self.emergency_exit("Враг в локале!")
            return

        # 3. БОЕВАЯ ЛОГИКА
        if state.npc_all:
            self.no_targets_timer = None
            state.combat_mode_active = "COMBAT"
            self._combat_logic()
        else:
            # Целей нет — проверяем, нужно ли возвращать дронов
            self._handle_drones_recall()
            state.combat_mode_active = "SCANNING"
            # self._find_new_anomaly()

    def _check_and_repair(self):
        """Включаем репку горячей клавишей, если она выключена"""
        # Если в словаре repairers есть хотя бы одна репка в статусе READY
        for name, status in self.current_state.repairers.items():
            if status == "READY":
                print(f"[Bot] Репка {name} выключена. Нажимаю F1")
                pyautogui.press('f1') 
                time.sleep(3)
                # Делаем break, чтобы не спамить всеми кнопками сразу, 
                # если репок несколько — включим следующую на следующем тике.
                break 

    def _combat_logic(self):
        state = self.current_state
        off_x, off_y = self._get_offset()
        
        # а) Двигатель (болванка под горячую клавишу, например MWD на Alt+F1)
        if not state.is_orbiting: # Если мы еще не на орбите
             # pyautogui.hotkey('f2') 
             print(f"No oribiting detected - consider activating MWD and orbit")
        else:
             #print(f"Already orbiting target")
             pass

        # б) Захват целей (Приоритет: Фрегаты)
        # Если у нас нет захваченных целей в state.targets, пробуем захватить из овервью
        if not state.targets and state.npc_all:
            # Формируем список: сначала фрегаты, потом остальные, берем 3
            targets_to_lock = (state.npc_frigate + state.npc_all)[:3]
            print(f"[Bot] Захват {len(targets_to_lock)} целей с рандомизацией...")
            # print(f"[Bot] Движение к {len(targets_to_lock)} целям (тест координат)...")
            #print(f"[Bot] Захватываю {len(targets_to_lock)} целей через Ctrl+LKM")
            
            pyautogui.keyDown('ctrl')
            for lx, ly in targets_to_lock:
                # 1. Рандомное смещение по X (вправо от 5 до 30 пикселей)
                # Чтобы не кликать точно в край иконки, а попадать в текст/полосу
                rand_x_offset = random.randint(5, 30)
                # Небольшой разброс по Y (на всякий случай +- 2 пикселя)
                rand_y_offset = random.randint(-2, 2)

                target_x = lx + off_x + rand_x_offset
                target_y = ly + off_y + rand_y_offset
                
                # 2. Рандомная скорость (быстрее, от 0.15 до 0.4 секунды)
                rand_duration = random.uniform(0.15, 0.4)
                
                # Двигаем курсор
                pyautogui.moveTo(target_x, target_y, duration=rand_duration)
                
                # 3. Рандомная пауза перед кликом (имитируем реакцию человека)
                time.sleep(random.uniform(0.1, 0.3))
                
                # Теперь можно кликать (Ctrl все еще зажат)
                pyautogui.click() 
                
                # Короткая пауза после клика
                time.sleep(random.uniform(0.2, 0.5))
                
            pyautogui.keyUp('ctrl')

        # в) Логика дронов (только если выбран режим DRONES)
        if self.use_drones and state.targets:
            # 1. Если дроны еще в ангаре — выпускаем
            if state.drones_in_bay > 0 and state.drones_in_space == 0:
                print("[Bot] Выпускаю дронов (Shift+F)")
                pyautogui.keyDown('shift')
                time.sleep(0.1)  # Даем игре время понять, что Shift зажат
                pyautogui.press('f')
                time.sleep(0.1)
                pyautogui.keyUp('shift')
                time.sleep(5)  # Ждем, пока дроны выпустятся

            # 2. Если дроны не воюют — отправляем в атаку
            if not state.drones_fighting:
                print("[Bot] Дроны в атаку! (F)")
                pyautogui.press('f')
                time.sleep(1)

    def _handle_drones_recall(self):
        """Логика сбора дронов, если целей нет более 10 секунд"""
        state = self.current_state
        
        # Если дроны в космосе и целей нет
        if state.drones_in_space > 0 and not state.npc_all:
            if self.no_targets_timer is None:
                self.no_targets_timer = time.time()
                print("[Bot] Целей нет, запустил таймер возврата дронов...")
            
            # Проверяем, прошло ли 10 секунд
            elapsed = time.time() - self.no_targets_timer
            if elapsed >= 10.0:
                if not state.drones_returning:
                    print(f"[Bot] Целей нет {int(elapsed)}с. Собираю дронов (Shift+R)")
                    pyautogui.keyDown('shift')
                    time.sleep(0.1)  # Даем игре время понять, что Shift зажат
                    pyautogui.press('r')
                    time.sleep(0.1)
                    pyautogui.keyUp('shift')
                    time.sleep(5)  # Ждем, пока дроны придут
                    self.no_targets_timer = None # Сбрасываем таймер после команды
        else:
            self.no_targets_timer = None
    
    def _find_new_anomaly(self):
        """Логика поиска аномалий (болванки)"""
        # а) Alt+P
        # б) Список аномалий
        # в) Варп через ПКМ
        pass

    def emergency_exit(self, reason):
        print(f"[!!!] EMERGENCY: {reason}")