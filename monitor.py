# -*- coding: utf-8 -*-
"""Мониторинг TCP-канала state из main.py (:5555).

Отдельный процесс для второго терминала. Подключается к 127.0.0.1:5555
и печатает живую сводку состояния: HP, движение, NPC, лок, цели, модули,
рекомендация executor'а. Ничего не отправляет, только читает.

Запуск:
    python monitor.py                 # одна строка на тик, обновление in-place
    python monitor.py --full          # полный JSON каждого N-го тика
    python monitor.py --full-every 30 # полный JSON каждые 30 сек
    python monitor.py --no-color      # без ANSI-цветов (для логов)
    python monitor.py --host 127.0.0.1 --port 5555

Ctrl+C — выход.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from typing import Optional


# ==================== ANSI ====================

class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    GREY = "\033[90m"


def _disable_colors():
    for k in dir(C):
        if not k.startswith("_") and isinstance(getattr(C, k), str):
            setattr(C, k, "")


# ==================== ФОРМАТИРОВАНИЕ ====================

def _colored_pct(pct: Optional[float], colors: bool) -> str:
    if pct is None:
        return f"{C.GREY}n/a{C.RESET}"
    if not colors:
        return f"{pct:5.1f}"
    if pct <= 25:
        c = C.RED
    elif pct <= 50:
        c = C.YELLOW
    else:
        c = C.GREEN
    return f"{c}{pct:5.1f}{C.RESET}"


def _bool_mark(v) -> str:
    return f"{C.GREEN}●{C.RESET}" if v else f"{C.GREY}○{C.RESET}"


def _movement(d: dict) -> str:
    m = d.get("movement") or {}
    if m.get("is_warp"):
        return f"{C.BLUE}WARP{C.RESET}"
    if m.get("is_establishing"):
        return f"{C.BLUE}ALIGN{C.RESET}"
    if m.get("is_orbiting"):
        return f"{C.CYAN}ORBIT{C.RESET}"
    if m.get("is_docked"):
        return f"{C.GREY}DOCKED{C.RESET}"
    if (d.get("speed_mps") or 0) > 0.5:
        return f"{C.CYAN}MOVE{C.RESET}"
    return f"{C.GREY}STILL{C.RESET}"


def format_line(state: dict, colors: bool = True) -> str:
    """Одна строка сводки (для in-place обновления)."""
    meta = state.get("meta") or {}
    ship = state.get("ship") or {}
    hp = ship.get("hp") or {}
    drones = state.get("drones") or {}
    npc = state.get("npc") or {}
    tgt = state.get("targets") or {}
    local = state.get("local_chat") or {}
    exec_ = state.get("executor") or {}
    scanner = state.get("scanner") or []
    menu = state.get("context_menu") or {}

    # HP
    sh = _colored_pct(hp.get("shield_pct"), colors)
    ar = _colored_pct(hp.get("armor_pct"), colors)
    ca = _colored_pct(hp.get("capacitor_pct"), colors)

    # Локальный чат: threat
    threat = local.get("threat") or "SAFE"
    if threat == "HOSTILE":
        threat_s = f"{C.RED}{C.BOLD}HOSTILE{C.RESET}"
    elif threat == "NEUTRAL":
        threat_s = f"{C.YELLOW}NEUTRAL{C.RESET}"
    else:
        threat_s = f"{C.GREEN}SAFE{C.RESET}"

    # NPC
    npc_count = npc.get("count", 0)
    frig = len(npc.get("frigates") or [])
    npc_s = f"{npc_count}(f{frig})" if npc_count else "0"

    # Цели
    tgt_s = f"{tgt.get('count', 0)}"

    # Дроны
    dr_s = f"{drones.get('in_bay', 0)}b/{drones.get('in_space', 0)}s"

    # executor
    action = exec_.get("recommended_action") or "IDLE"
    if action == "EMERGENCY_EXIT":
        act_s = f"{C.RED}{action}{C.RESET}"
    elif action == "IDLE":
        act_s = f"{C.GREY}{action}{C.RESET}"
    else:
        act_s = f"{C.YELLOW}{action}{C.RESET}"

    # время
    t = (meta.get("datetime") or "")[-8:]  # HH:MM:SS
    loading = f"{C.MAGENTA}LOAD{C.RESET}" if meta.get("is_loading") else ""

    # Меню
    menu_s = f"{C.MAGENTA}MENU{C.RESET}" if menu.get("open") else ""

    parts = [
        f"{C.GREY}{t}{C.RESET}",
        f"HP[{sh}/{ar}/{ca}]",
        _movement(ship),
        f"cap_d={_bool_mark(ship.get('movement', {}).get('is_docked'))}",
        f"npc={npc_s}",
        f"tgt={tgt_s}",
        f"dr={dr_s}",
        f"scan={len(scanner)}",
        f"local={threat_s}",
        f"NN={act_s}",
        loading,
        menu_s,
    ]
    return "  ".join(p for p in parts if p)


def format_full(state: dict) -> str:
    """Полный JSON (для отладки канала)."""
    return json.dumps(state, ensure_ascii=False, indent=2)


# ==================== СЕТЬ ====================

def connect_with_retry(host: str, port: int, label: str) -> socket.socket:
    """Подключиться к host:port, повторять каждые 2 сек."""
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((host, port))
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            print(f"[monitor] {label} connected to {host}:{port}")
            return s
        except (ConnectionRefusedError, OSError):
            print(f"[monitor] {label} waiting for {host}:{port} ...")
            time.sleep(2.0)


# ==================== MAIN ====================

def main():
    p = argparse.ArgumentParser(description="Monitor EVE state from main.py (:5555)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5555)
    p.add_argument("--full", action="store_true",
                   help="печатать полный JSON вместо одной строки")
    p.add_argument("--full-every", type=int, default=0,
                   help="каждые N секунд печатать полный JSON (0 = выкл)")
    p.add_argument("--no-color", action="store_true")
    args = p.parse_args()

    colors = not args.no_color
    if not colors:
        _disable_colors()

    sock = connect_with_retry(args.host, args.port, "state")

    buf = b""
    count = 0
    t_start = time.time()
    t_last_full = 0.0

    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                print("[monitor] connection closed")
                break
            buf += chunk
            # Обрабатываем все завершённые строки, оставляем хвост в buf
            last_state = None
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    state = json.loads(line)
                except json.JSONDecodeError:
                    continue
                last_state = state
                count += 1

            if last_state is None:
                continue

            now = time.time()

            # Полный JSON по флагу --full
            if args.full:
                print(format_full(last_state))
                print()
                continue

            # In-place строка (перезапись)
            line = format_line(last_state, colors=colors)
            sys.stdout.write("\r" + line + "   ")
            sys.stdout.flush()

            # Периодический полный JSON
            if args.full_every > 0 and now - t_last_full >= args.full_every:
                t_last_full = now
                sys.stdout.write("\n")
                print(f"--- full state @ {time.strftime('%H:%M:%S')} ---")
                print(format_full(last_state))
                print("--- end full ---")

    except KeyboardInterrupt:
        print("\n[monitor] stopping")
    finally:
        try:
            sock.close()
        except Exception:
            pass
        elapsed = time.time() - t_start
        rate = count / elapsed if elapsed > 0 else 0.0
        print(f"[monitor] received {count} states in {elapsed:.1f}s ({rate:.1f}/s)")


if __name__ == "__main__":
    main()