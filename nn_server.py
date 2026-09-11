# -*- coding: utf-8 -*-
"""nn_server — ЗАГЛУШКА нейросети.

Отдельный процесс, работает независимо от main.py.
Читает состояние игры по TCP 127.0.0.1:5555 (построчный JSON от StateBroadcaster)
и отправляет решения по TCP 127.0.0.1:5556 (ActionReceiver, JSON + "\\n").

Позже predict() заменяется реальной моделью (ONNX/PyTorch) поверх
state['nn_features']. Ничего не пишет на диск и не импортирует модули бота.
"""
import argparse
import json
import socket
import sys
import time
from typing import Optional

_NO_STATE_WARN_PERIOD = 10.0   # период предупреждения "нет state"
_STATS_PERIOD = 30.0           # период печати статистики


def connect_with_retry(host: str, port: int, label: str) -> socket.socket:
    """Подключается к host:port, повторяя попытки каждые 2 секунды."""
    while True:
        try:
            sock = socket.create_connection((host, port), timeout=3)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            print(f"[NN] connected to {label} {host}:{port}")
            return sock
        except OSError:
            print(f"[NN] waiting for main.py on :{port} ...", flush=True)
            time.sleep(2.0)


def predict(state: dict) -> dict:
    """Заглушка нейросети. Простые эвристики — чтобы видеть канал.

    Позже здесь будет реальная модель (ONNX/PyTorch), принимающая
    state['nn_features'] и возвращающая action.
    """
    exec_ = state.get('executor') or {}
    threat = exec_.get('threat_level', 0)
    npc_count = (state.get('npc') or {}).get('count', 0)
    targets = (state.get('targets') or {}).get('count', 0)
    drones_space = (state.get('drones') or {}).get('in_space', 0)

    if threat >= 2:
        return {'action': 'EMERGENCY_EXIT',
                'reason': 'hostile in local (stub)'}
    if threat == 1:
        return {'action': 'IDLE',
                'reason': 'neutral in local (stub)'}
    if npc_count > 0 and targets < 3:
        return {'action': 'ENGAGE_TARGETS',
                'reason': f'{npc_count} npc, {targets} locked (stub)',
                'params': {'max_targets': 3}}
    if drones_space > 0 and npc_count == 0:
        return {'action': 'RECALL_DRONES',
                'reason': f'{drones_space} drones no targets (stub)'}
    return {'action': 'IDLE',
            'reason': 'nothing to do (stub)'}


def main() -> None:
    """Точка входа: подключение к обоим каналам и цикл state -> predict -> action."""
    parser = argparse.ArgumentParser(description="NN stub: state :5555 -> actions :5556")
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port-state', type=int, default=5555)
    parser.add_argument('--port-action', type=int, default=5556)
    parser.add_argument('--interval', type=float, default=0.5,
                        help='минимальный интервал между отправками action, сек')
    args = parser.parse_args()

    state_sock = connect_with_retry(args.host, args.port_state, 'state')
    action_sock = connect_with_retry(args.host, args.port_action, 'action')

    received = 0
    sent = 0
    last_action_ts = 0.0        # троттлинг отправки
    last_state_ts = time.time() # для детекта "нет state"
    last_no_state_warn = 0.0    # троттлинг предупреждения
    last_stats_ts = time.time() # троттлинг статистики
    buf = b""

    try:
        while True:
            # --- неблокирующий отсчёт "нет state >10с" ---
            now = time.time()
            if now - last_state_ts > _NO_STATE_WARN_PERIOD and now - last_no_state_warn >= _NO_STATE_WARN_PERIOD:
                last_no_state_warn = now
                print("[NN] no state >10s", flush=True)

            if now - last_stats_ts >= _STATS_PERIOD:
                last_stats_ts = now
                print(f"[NN] states received: {received}, actions sent: {sent}", flush=True)

            # --- приём state (блокирующий recv до 1 сек) ---
            state_sock.settimeout(1.0)
            try:
                chunk = state_sock.recv(4096)
                if not chunk:
                    print("[NN] state channel closed, stopping", flush=True)
                    break
                buf += chunk
                last_state_ts = time.time()
            except socket.timeout:
                continue
            except OSError:
                print("[NN] state channel error, stopping", flush=True)
                break

            # --- разбор строк ---
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    state = json.loads(line.decode('utf-8'))
                except (ValueError, UnicodeDecodeError):
                    continue  # некорректный state — пропустить
                received += 1
                action = predict(state)

                now = time.time()
                if now - last_action_ts < args.interval:
                    continue  # троттлинг: не чаще action_interval
                last_action_ts = now
                try:
                    action_sock.sendall((json.dumps(action, ensure_ascii=False) + "\n").encode('utf-8'))
                except OSError:
                    print("[NN] action channel error, stopping", flush=True)
                    raise SystemExit(1)
                sent += 1
                print(f"[NN] sent action={action.get('action')} reason={action.get('reason', '')} (state #{received})",
                      flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            state_sock.close()
        except OSError:
            pass
        try:
            action_sock.close()
        except OSError:
            pass

    print("[NN] stopping")
    print(f"[NN] received {received} states, sent {sent} actions")


if __name__ == "__main__":
    main()
