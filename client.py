#reading memory with eveui.exe
import json
import os
import subprocess
import threading
import sys
from queue import Queue, Empty

from parser import EveParser                            # СТАРЫЙ парсер — результат для бота
from new_parser import EveParserNew, compare_parsers    # НОВЫЙ парсер — параллельный разбор для сравнения


class EveClient:
    def __init__(self, exe_path, comparison_callback=None, state_callback=None):
        self.exe_path = exe_path
        # Очередь для бота: сюда кладётся ТОЛЬКО результат СТАРОГО парсера (state, draw_list)
        self.queue = Queue(maxsize=2)
        # Внутренняя очередь сырых JSON между читающим потоком и потоком парсинга
        self._raw_queue = Queue(maxsize=3)
        self.running = True

        # Оба парсера работают на одних и тех же JSON-данных:
        self.parser = EveParser()          # старый рабочий парсер (bot.py не меняется)
        self.parser_new = EveParserNew()   # новый парсер локального чата

        # Callback для передачи результата сравнения в GUI (main.py)
        self.comparison_callback = comparison_callback
        # Callback для передачи ПОЛНОГО состояния нового парсера в GUI (окно отладки)
        self.state_callback = state_callback

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

        # Отдельный поток: парсит каждый JSON ОБОИМИ парсерами
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
        """Параллельный разбор каждого JSON:
        1) старый парсер -> очередь для бота (main.py / bot.py работают как раньше)
        2) новый парсер  -> сравнение результатов -> callback в GUI
        """
        while self.running:
            try:
                data = self._raw_queue.get(timeout=0.5)
            except Empty:
                continue

            # 1. СТАРЫЙ парсер — его результат уходит боту
            try:
                old_state, draw_list = self.parser.parse(data)
            except Exception as e:
                print(f"[Client] OLD parser error: {e}")
                continue

            # 2. НОВЫЙ парсер — те же самые данные, параллельно
            new_state = None
            try:
                new_state, _new_draw_list = self.parser_new.parse(data)
            except Exception as e:
                print(f"[Client] NEW parser error: {e}")

            # 2b. Полное состояние нового парсера — в GUI для окна отладки
            if new_state is not None and self.state_callback:
                try:
                    self.state_callback(new_state)
                except Exception as e:
                    print(f"[Client] State callback error: {e}")

            # 3. Сравнение результатов двух парсеров
            comparison = compare_parsers(old_state, new_state)
            if self.comparison_callback:
                try:
                    self.comparison_callback(comparison)
                except Exception as e:
                    print(f"[Client] Comparison callback error: {e}")

            # 4. В очередь для бота — ТОЛЬКО результат старого парсера
            self._put_drop_oldest(self.queue, (old_state, draw_list))

    
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