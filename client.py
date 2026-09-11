#reading memory with eveui.exe
import json
import os
import subprocess
import threading
import sys
import time
from queue import Queue, Empty

from new_parser import EveParserNew         # ЕДИНСТВЕННЫЙ парсер UI
from state_json import build_state_payload   # JSON-выгрузка для бота и нейросети


class EveClient:
    def __init__(self, exe_path, state_callback=None):
        self.exe_path = exe_path
        # Очередь для бота: сюда кладётся (state, draw_list, payload) нового парсера
        self.queue = Queue(maxsize=2)
        # Внутренняя очередь сырых JSON между читающим потоком и потоком парсинга
        self._raw_queue = Queue(maxsize=3)
        self.running = True

        # Единственный парсер системы (см. new_parser.py)
        self.parser = EveParserNew()

        # Callback для передачи ПОЛНОГО состояния парсера в GUI (окно отладки)
        self.state_callback = state_callback

        # Последний собранный JSON payload (для внешних потребителей)
        self.last_payload = None
        # Последний СЫРОЙ JSON-дамп UI (для кнопки "Выгрузить сырой JSON")
        self.last_raw_json = None
        # Расширенное логирование (чекбокс VERBOSE LOG в GUI)
        self.verbose = False
        self._last_vlog_ts = 0.0
        self.broadcaster = None   # устанавливается из main.py, может быть None

    def _log_reader(self, pipe):
        for line in pipe:
            sys.stderr.write(f"  [Go-Log]: {line}")
            sys.stderr.flush()

    def _put_drop_oldest(self, q, item):
        """Кладём в очередь, при переполнении вытесняя самый старый элемент."""
        if q.full():
            try: q.get_nowait()
            except Empty: pass
        q.put(item)

    def reader_loop(self):
        print(f"[Client] Запуск процесса: {self.exe_path}")
        process = subprocess.Popen(
            [self.exe_path, "-remove-other-dict-entries"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace', bufsize=10**7
        )
        self.process = process

        threading.Thread(target=self._log_reader, args=(process.stderr,), daemon=True).start()

        # Отдельный поток: парсит каждый JSON парсером EveParserNew
        threading.Thread(target=self._parse_loop, daemon=True).start()

        while self.running:
            line = process.stdout.readline()
            if not line: break
            line_s = line.strip()
            if not line_s.startswith("{"):
                if line_s: print(f"[Go-Stdout]: {line_s}")
                continue

            try:
                # Сырой JSON уходит в поток парсинга (чтение потока не блокируется)
                data = json.loads(line_s)
                self._put_drop_oldest(self._raw_queue, data)
            except Exception as e:
                print(f"[Client] JSON Error: {e}")

    def _parse_loop(self):
        """Разбор каждого JSON единым парсером (EveParserNew):
        1) state (ParsedUIState) + draw_list -> очередь для бота и GUI;
        2) state -> callback в GUI (окно отладки парсера);
        3) build payload -> JSON-файл state_payload.json для исполнительного
           бота и нейросети (см. state_json.py).
        """
        while self.running:
            try:
                data = self._raw_queue.get(timeout=0.5)
            except Empty:
                continue

            # Запоминаем сырой дамп — его отдаёт кнопка "Выгрузить сырой JSON"
            self.last_raw_json = data

            # 1. Парсинг UI
            try:
                state, draw_list = self.parser.parse(data)
            except Exception as e:
                print(f"[Client] parser error: {e}")
                continue

            # Расширенный лог: сводка разбора (не чаще раза в 2 сек)
            if self.verbose:
                now_t = time.time()
                if now_t - self._last_vlog_ts >= 2.0:
                    self._last_vlog_ts = now_t
                    print(f"[V][Client] parsed: scan={len(state.scan_results or [])} "
                          f"npc={len(state.npc_all or [])} targets={len(state.targets or [])} "
                          f"menu={state.menu_open} local={state.local_player_count} "
                          f"loading={state.is_loading} docked={state.is_docked}")

            # 2. JSON-выгрузка для исполнительного бота и нейросети
            payload = None
            try:
                payload = build_state_payload(state, draw_list)
                self.last_payload = payload
                if self.broadcaster is not None:
                    self.broadcaster.broadcast(payload)
            except Exception as e:
                print(f"[Client] payload error: {e}")

            # 3. Полное состояние — в GUI для окна отладки
            if self.state_callback:
                try:
                    self.state_callback(state)
                except Exception as e:
                    print(f"[Client] State callback error: {e}")

            # 4. В очередь для бота — (state, draw_list, payload)
            self._put_drop_oldest(self.queue, (state, draw_list, payload))

    def dump_raw_json(self, out_dir: str = '.') -> str:
        """Выгрузка последнего сырого JSON-дампа UI в файл (для отладки парсера).

        Вызывается кнопкой "ВЫГРУЗИТЬ СЫРОЙ JSON" в GUI (main.py).
        :return: путь созданного файла
        :raises RuntimeError: если от eveui.exe ещё не приходило ни одного дампа
        """
        if self.last_raw_json is None:
            raise RuntimeError("Сырой JSON ещё не получен от eveui.exe")
        fn = os.path.join(out_dir, f"raw_dump_{time.strftime('%Y%m%d_%H%M%S')}.json")
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(self.last_raw_json, f, ensure_ascii=False, indent=2)
        return fn


    
    def stop(self):
        """Гарантированная остановка процесса eveui.exe"""
        self.running = False
        
        # 1. Сначала пробуем через PID (если процесс сохранен в self.process)
        if hasattr(self, 'process') and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.kill()
            except:
                pass

        # 2. ФИНАЛЬНЫЙ УДАР: Принудительно убиваем по имени файла через систему
        # Это сработает, даже если self.process потерялся или завис
        try:
            # /F - принудительно, /IM - по имени образа, /T - дерево процессов
            subprocess.run(['taskkill', '/F', '/IM', 'eveui.exe', '/T'], 
                           capture_output=True, check=False)
            print("[Client] Taskkill command executed for eveui.exe")
        except Exception as e:
            print(f"[Client] Taskkill failed: {e}")