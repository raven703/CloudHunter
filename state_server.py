# -*- coding: utf-8 -*-
"""Два TCP-сервера на loopback для обмена данными с внешним процессом (nn_server.py).

- StateBroadcaster (127.0.0.1:5555) — рассылка последнего payload всем клиентам.
- ActionReceiver   (127.0.0.1:5556) — приём JSON-команд от nn_server.py.

Обмен — построчный: JSON + "\\n". Ничего не логирует (решает вызывающий код).
"""
import socket
import json
import threading
import time
from typing import Dict, List, Optional

_HOST = "127.0.0.1"
_BROADCAST_PORT = 5555
_ACTIONS_PORT = 5556


class StateBroadcaster:
    """TCP-сервер рассылки состояния (порт 5555).

    Клиенты только слушают: поток-приёмник для них не нужен.
    """

    def __init__(self, host: str = _HOST, port: int = _BROADCAST_PORT):
        self._clients: List[socket.socket] = []
        self._lock = threading.Lock()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(5)
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def _accept_loop(self) -> None:
        """Принимает подключения, TCP_NODELAY на клиентских сокетах."""
        while True:
            try:
                client, _addr = self._sock.accept()
            except OSError:
                break  # сокет закрыт (stop())
            try:
                client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            with self._lock:
                self._clients.append(client)

    def broadcast(self, payload: dict) -> None:
        """Рассылает payload (json + "\\n") всем клиентам; ошибки — клиент удаляется."""
        if not payload:
            return
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            if not self._clients:
                return  # клиентов нет — молча ничего не делаем
            alive: List[socket.socket] = []
            for client in self._clients:
                try:
                    client.sendall(data)
                    alive.append(client)
                except OSError:
                    try:
                        client.close()
                    except OSError:
                        pass
            self._clients = alive

    def client_count(self) -> int:
        """Сколько клиентов подключено."""
        with self._lock:
            return len(self._clients)

    def stop(self) -> None:
        """Закрывает слушающий сокет и всех клиентов."""
        with self._lock:
            for client in self._clients:
                try:
                    client.close()
                except OSError:
                    pass
            self._clients = []
        try:
            self._sock.close()
        except OSError:
            pass


class ActionReceiver:
    """TCP-сервер приёма JSON-команд (порт 5556).

    Хранит только последнюю команду (перезапись, не очередь).
    """

    def __init__(self, host: str = _HOST, port: int = _ACTIONS_PORT):
        self._latest: Optional[Dict] = None
        self._latest_ts: float = 0.0
        self._lock = threading.Lock()
        self._clients: List[socket.socket] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(5)
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def _accept_loop(self) -> None:
        """Принимает подключения и запускает поток-приёмник для каждого."""
        while True:
            try:
                client, _addr = self._sock.accept()
            except OSError:
                break  # сокет закрыт (stop())
            try:
                client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            with self._lock:
                self._clients.append(client)
            threading.Thread(target=self._recv_loop, args=(client,), daemon=True).start()

    def _recv_loop(self, client: socket.socket) -> None:
        """Читает поток, режет по b"\\n", разбирает JSON; некорректный — пропускает."""
        buf = b""
        while True:
            try:
                chunk = client.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    cmd = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue  # некорректный JSON — пропустить, не падать
                if isinstance(cmd, dict):
                    with self._lock:
                        self._latest = cmd
                        self._latest_ts = time.time()
        # ошибка recv или disconnect — закрыть сокет, удалить из списка
        try:
            client.close()
        except OSError:
            pass
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)

    def get_latest(self) -> Optional[Dict]:
        """Возвращает и обнуляет последнюю команду (одноразовое чтение)."""
        with self._lock:
            cmd = self._latest
            self._latest = None
            return cmd

    def last_seen_ts(self) -> float:
        """Время (time.time()) последней полученной команды; 0.0 если не было."""
        with self._lock:
            return self._latest_ts

    def client_count(self) -> int:
        """Сколько клиентов подключено."""
        with self._lock:
            return len(self._clients)

    def stop(self) -> None:
        """Закрывает слушающий сокет и всех клиентов."""
        with self._lock:
            for client in self._clients:
                try:
                    client.close()
                except OSError:
                    pass
            self._clients = []
        try:
            self._sock.close()
        except OSError:
            pass


if __name__ == "__main__":
    # для ручного теста: поднимаем оба сервера, ждём Ctrl+C
    import time as _t
    b = StateBroadcaster()
    r = ActionReceiver()
    print("State :5555, Actions :5556. Ctrl+C для выхода.")
    try:
        while True:
            _t.sleep(1.0)
    except KeyboardInterrupt:
        b.stop(); r.stop()
