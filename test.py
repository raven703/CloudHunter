import json, time
from parser import parse_ui_root
from bot import EveBot

with open("raw_dump_20260911_095055.json", encoding="utf-8") as f:
    data = json.load(f)

state = parse_ui_root(data)
print("lock_range_km:", state.lock_range_km)
print("overview_entries:", len(state.overview_entries))
print("npc_all:", len(state.npc_all))
print("targets:", len(state.targets))

# Имитируем бота: вызываем _npc_distances и _lock_candidates
bot = EveBot()
bot.current_state = state

# 1. _npc_distances
nd = bot._npc_distances(state)
print(f"\n_npc_distances: {len(nd)} записей")
for name, dist, x, y in nd[:5]:
    print(f"  {name!r:35} {dist/1000:6.1f} км @ ({x:.0f},{y:.0f})")

# 2. Сопоставление координат npc_all <-> overview_entries
npc_set = {(round(x), round(y)) for x, y in state.npc_all}
ov_set  = {(round(x), round(y)) for _, _, x, y in nd}
print(f"\nКоординаты npc_all: {len(npc_set)}, overview: {len(ov_set)}, "
      f"совпало: {len(npc_set & ov_set)}")

# 3. _lock_candidates с фильтром
cand = bot._lock_candidates(state)
print(f"\n_lock_candidates: {len(cand)}")
for c in cand[:5]:
    print(f"  {c}")