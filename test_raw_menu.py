# -*- coding: utf-8 -*-
"""Проверка свежих raw_dump: контекстное меню аномалий, сканер, selected item."""
import glob
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from full_parser import parse_ui_root, parse_context_menu

for fn in sorted(glob.glob("raw_dump_*.json")):
    data = json.load(open(fn, encoding="utf-8"))
    st = parse_ui_root(data)
    menu = parse_context_menu(data)
    print("===", fn)
    print(f"  scanner rows: {len(st.scan_results)}")
    for r in st.scan_results[:6]:
        print(f"    {r.get('id')} | {r.get('group')} | {r.get('name') or '-'} | "
              f"x={r.get('x')} y={r.get('y')} h={r.get('h')}")
    print(f"  menu_open={menu['open']} menu_type={menu['menu_type']} items={len(menu['items'])}")
    for it in menu['items'][:14]:
        if it.get('type') == 'entry':
            print(f"    [{it.get('text')}] @({it.get('x')},{it.get('y')}) "
                  f"w={it.get('w')} h={it.get('h')} submenu={it.get('has_submenu')}")
        else:
            print("    ---")
    si = st.selected_item
    print(f"  selected_item: {si.get('name') if si else None}, "
          f"dist={si.get('distance') if si else '-'}, "
          f"actions={[a.get('action') for a in (si.get('actions') or [])] if si else []}")
