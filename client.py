#reading memory with eveui.exe
import json
import os
import subprocess
import threading
import sys
from queue import Queue, Empty

class EveClient:
    def __init__(self, exe_path):
        self.exe_path = exe_path
        self.queue = Queue(maxsize=2)
        self.running = True

    def _log_reader(self, pipe):
        for line in pipe:
            sys.stderr.write(f"  [Go-Log]: {line}")
            sys.stderr.flush()

    def reader_loop(self):
        print(f"[Client] Запуск процесса: {self.exe_path}")
        process = subprocess.Popen(
            [self.exe_path, "-remove-other-dict-entries"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace', bufsize=10**7
        )

        threading.Thread(target=self._log_reader, args=(process.stderr,), daemon=True).start()

        while self.running:
            line = process.stdout.readline()
            if not line: break
            line_s = line.strip()
            if not line_s.startswith("{"):
                if line_s: print(f"[Go-Stdout]: {line_s}")
                continue

            try:
                #print(f"[Debug] Packet: {len(line_s) // 1024} KB")
                data = json.loads(line_s)
                if self.queue.full():
                    try: self.queue.get_nowait()
                    except Empty: pass
                self.queue.put(data)
            except Exception as e:
                print(f"[Client] JSON Error: {e}")

    
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