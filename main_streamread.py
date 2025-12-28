import json
import subprocess
import threading
import sys
import time


class EveUIStreamReader:
    def __init__(self, exe_path, args=None, callback=None):
        self.exe_path = exe_path
        self.args = args or []
        self.callback = callback
        self.process = None
        self.thread = None
        self.running = False

    def start(self):
        """Запускает eveui.exe в отдельном окне и начинает читать stdout."""
        if self.running:
            return

        self.running = True

        # Запуск eveui.exe в отдельном окне консоли
        self.process = subprocess.Popen(
            [self.exe_path] + self.args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            creationflags=subprocess.CREATE_NEW_CONSOLE  # ← отдельная консоль
        )

        print("[Python] eveui.exe started, reading stream...")

        self.thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.thread.start()

        # Отдельный поток для чтения stderr
        threading.Thread(target=self._stderr_loop, daemon=True).start()

    def _reader_loop(self):
        """Читает JSON-объекты построчно."""
        for line in self.process.stdout:
            if not self.running:
                break

            line = line.strip()
            if not line:
                continue

            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                print("[Python] Error in JSON:", line)
                continue

            if self.callback:
                self.callback(obj)

    def _stderr_loop(self):
        """Печатает stderr Go-приложения."""
        for line in self.process.stderr:
            print("[eveui stderr]", line.strip())

    def stop(self):
        self.running = False
        if self.process:
            self.process.terminate()
        if self.thread:
            self.thread.join()


# ------------------------------------------------------------
# Пример использования
# ------------------------------------------------------------

def on_ui_update(obj):
    print("[Python] Frame receive:", obj.get("pythonObjectTypeName"))


def main():
    reader = EveUIStreamReader(
        exe_path="eveui.exe",
        
        callback=on_ui_update
    )

    reader.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Python] Stopping...")
        reader.stop()
        sys.exit(0)


if __name__ == "__main__":
    main()
