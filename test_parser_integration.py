# -*- coding: utf-8 -*-
"""Интеграционная проверка парсера БЕЗ игры.

Прогоняет все uiroot*.json дампы через EveParserNew (new_parser.py),
собирает JSON payload (state_json.py) и печатает ключевые поля.

Использование:
    python test_parser_integration.py                 # все uiroot*.json
    python test_parser_integration.py uiroot_new.json # конкретный дамп
"""
import glob
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from new_parser import EveParserNew
from state_json import build_state_payload, state_payload_json


def run(fn: str) -> None:
    with open(fn, "r", encoding="utf-8") as f:
        data = json.load(f)

    parser = EveParserNew()
    state, draw_list = parser.parse(data)
    payload = build_state_payload(state, draw_list)

    # JSON-структура должна быть строго сериализуемой
    js = state_payload_json(state, draw_list)
    parsed_back = json.loads(js)

    print(f"\n=== {fn} ===")
    print(f"  window: {state.window_width}x{state.window_height}  loading: {state.is_loading}")
    hp = payload['ship']['hp']
    print(f"  HP: shield={hp['shield_pct']} armor={hp['armor_pct']} "
          f"struct={hp['structure_pct']} cap={hp['capacitor_pct']}")
    print(f"  speed: {payload['ship']['speed_mps']} m/s  movement: {payload['ship']['movement']['state']}")
    print(f"  modules: {payload['modules']['status']}")
    print(f"  repairers: {payload['modules']['repairers']}")
    print(f"  drones: bay={state.drones_in_bay} space={state.drones_in_space}/{state.drones_max} "
          f"fighting={state.drones_fighting} returning={state.drones_returning} "
          f"under_attack={state.drones_under_attack}")
    print(f"  targets: {payload['targets']['count']}  active={bool(state.active_target)}")
    print(f"  npc: total={payload['npc']['count']} "
          f"fr={len(state.npc_frigate)} cr={len(state.npc_cruiser)} "
          f"bcr={len(state.npc_battlecruiser)} bs={len(state.npc_battleship)}")
    print(f"  local: {state.local_player_count} players, neutral={state.local_has_neutral}, "
          f"hostile={state.local_has_hostile}")
    for p in state.local_players[:5]:
        print(f"     [{p.get('standing')}] {p.get('name')}")
    print(f"  overview: {len(state.overview_entries)}  scanner: {len(state.scan_results)}")
    print(f"  system: {state.current_system} (sec {state.security_status})")
    print(f"  draw_list: {len(draw_list)} rects (NPC/targets/pilots)")
    print(f"  executor: {payload['executor']['recommended_action']} "
          f"(threat={payload['executor']['threat_level']}, mode={payload['executor']['combat_mode']})")
    print(f"  nn_features: {len(parsed_back['nn_features'])} features")
    print(f"  payload top-level keys: {list(parsed_back.keys())}")
    print(f"  payload size: {len(js)} chars")

    # Payload для внешних потребителей
    out_fn = fn.rsplit('.', 1)[0] + "_payload.json"
    with open(out_fn, "w", encoding="utf-8") as f:
        f.write(js)
    print(f"  -> saved {out_fn}")


if __name__ == "__main__":
    files = sys.argv[1:] or sorted(glob.glob("uiroot*.json"))
    files = [f for f in files if "payload" not in f]
    for fn in files:
        try:
            run(fn)
        except Exception as e:
            print(f"\n=== {fn} ===\n  FAILED: {e}")
            import traceback
            traceback.print_exc()
