# -*- coding: utf-8 -*-
"""Одноразовая проверка: синтетические NPC в дампе -> классификация + executor."""
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from new_parser import EveParserNew
from state_json import build_state_payload


def inject_npc(node, label):
    """Помечаем SpaceObjectIcon как NPC (как это выглядит в реальных дампа)."""
    if node.get("pythonObjectTypeName") == "SpaceObjectIcon":
        ei = node.setdefault("dictEntriesOfInterest", {})
        ei["_setNameOverride"] = label   # в реальных дампах 'npc' приходит в значениях ei
        return True
    for ch in node.get("children", []) or []:
        if inject_npc(ch, label):
            return True
    return False


with open("uiroot_new.json", "r", encoding="utf-8") as f:
    data = json.load(f)

# Находим все SpaceObjectIcon верхнего уровня овервью и вешаем на них NPC-метки
count = 0
labels = ["npcfrigate", "npcbattleship", "npccruiser", "npcbattlecruiser", "npc"]

def walk(node):
    global count
    if not isinstance(node, dict):
        return
    if node.get("pythonObjectTypeName") == "SpaceObjectIcon" and count < len(labels):
        ei = node.setdefault("dictEntriesOfInterest", {})
        ei["_setNameOverride"] = labels[count]
        count += 1
        return
    for ch in node.get("children", []) or []:
        walk(ch)

walk(data)
print(f"Injected NPC labels: {count}")

state, draw_list = EveParserNew().parse(data)
print(f"npc_all:            {len(state.npc_all)}  -> {state.npc_all[:3]}")
print(f"npc_frigate:        {len(state.npc_frigate)}")
print(f"npc_battleship:     {len(state.npc_battleship)}")
print(f"npc_cruiser:        {len(state.npc_cruiser)}")
print(f"npc_battlecruiser:  {len(state.npc_battlecruiser)}")
print(f"combat_mode:        {state.combat_mode_active}")
npc_draw = [d for d in draw_list if d.get('label', '').startswith(('NPC', 'FRIGATE', 'CRUISER', 'BATTLE'))]
print(f"npc draw rects:     {len(npc_draw)} -> {[d['label'] for d in npc_draw]}")

payload = build_state_payload(state, draw_list)
ex = payload['executor']
print(f"executor: {ex['recommended_action']}  reason={ex['reason']}")
print(f"lock_candidates: {ex['lock_candidates']}")
print(f"npc payload: total={payload['npc']['count']} fr={len(payload['npc']['frigates'])} "
      f"bs={len(payload['npc']['battleships'])} cr={len(payload['npc']['cruisers'])} "
      f"bcr={len(payload['npc']['battlecruisers'])}")
print(f"nn_features npc: total={payload['nn_features']['npc_total']} "
      f"fr={payload['nn_features']['npc_frigates']} bs={payload['nn_features']['npc_battleships']}")

assert len(state.npc_all) == count, "npc_all count mismatch!"
assert len(state.npc_frigate) == 1, "frigate not classified!"
assert len(state.npc_battleship) == 1, "battleship not classified!"
assert len(state.npc_cruiser) == 1, "cruiser not classified!"
assert len(state.npc_battlecruiser) == 1, "battlecruiser not classified!"
assert ex['recommended_action'] == 'EMERGENCY_EXIT', "hostile in local must override combat!"
print("OK: SAFETY priority respected (hostile in local -> EMERGENCY_EXIT)")

# Имитируем безопасный локал и включённую репку — боевая логика должна предлагать захват целей
state.local_has_hostile = False
state.local_has_neutral = False
state.repairers = {name: 'ACTIVE' for name in state.repairers} or {}
payload = build_state_payload(state, draw_list)
ex = payload['executor']
print(f"executor (safe local, rep on): {ex['recommended_action']}  reason={ex['reason']}")
print(f"lock_candidates: {ex['lock_candidates']}")
assert ex['recommended_action'] == 'ENGAGE_TARGETS', "executor should engage!"
assert len(ex['lock_candidates']) == 3, "should propose 3 lock candidates!"
assert ex['lock_candidates'][0] == {"x": int(state.npc_frigate[0][0]), "y": int(state.npc_frigate[0][1])}, \
    "frigate must be first lock candidate!"
print("\nALL NPC INTEGRATION ASSERTIONS PASSED")
