import json
from parser import parse_ui_root

data = json.load(open('raw_dump_20260912_182519.json', encoding='utf-8'))
state = parse_ui_root(data)

print("=== OVERVIEW ENTRIES ===")
for e in state.overview_entries:
    print(f"  name={e.get('name')!r:30} x={e.get('x'):>6} y={e.get('y'):>6} "
          f"npc_class={e.get('npc_class')} type={e.get('type')} "
          f"dist={e.get('distance')}")

print("\n=== TARGETS ===")
for t in state.targets:
    print(f"  name={t.get('name')!r:30} x={t.get('x'):>6} y={t.get('y'):>6} "
          f"is_active={t.get('is_active')}")

print("\n=== SELECTED ITEM ===")
print(f"  {state.selected_item}")

print("\n=== STATUS ===")
print(f"  is_orbiting={state.is_orbiting} is_warp={state.is_warp} "
      f"is_establishing={state.is_establishing}")