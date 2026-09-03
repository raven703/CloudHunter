import json
from full_parser import get_val, _walk_all

d = json.load(open('raw_dump_20260902_191755.json','r',encoding='utf-8'))

def _find_all(node, out):
    if not isinstance(node, dict): return
    if node.get('pythonObjectTypeName') == 'Telecom': out.append(node)
    for c in node.get('children',[]) or []:
        _find_all(c, out)
    return

telecoms = []
_find_all(d, telecoms)
print(f'Total Telecom nodes: {len(telecoms)}')

for idx, tc in enumerate(telecoms):
    tc_ei = tc.get('dictEntriesOfInterest', {}) or {}
    tx, ty = get_val(tc_ei.get('_displayX',0)), get_val(tc_ei.get('_displayY',0))
    tw, th = get_val(tc_ei.get('_displayWidth',0)), get_val(tc_ei.get('_displayHeight',0))
    print(f'\n=== Telecom #{idx} @ ({tx},{ty}) {tw}x{th} ===')
    # Заголовок
    for sub in _walk_all(tc):
        sei = sub.get('dictEntriesOfInterest', {}) or {}
        txt = str(sei.get('_setText', '') or '').strip()
        if txt:
            t = sub.get('pythonObjectTypeName','')
            print(f'  first-text node: {t} txt={txt[:60]!r}')
            break
    # Полная цепочка до кнопок: печатаем _displayX/_displayY и _left/_top каждого узла
    def find_btn(node, px, py, chain):
        ei = node.get('dictEntriesOfInterest', {}) or {}
        t = node.get('pythonObjectTypeName', '')
        x = px + get_val(ei.get('_displayX', 0))
        y = py + get_val(ei.get('_displayY', 0))
        nm = str(ei.get('_name', ''))
        chain2 = chain + [(t, nm, get_val(ei.get('_displayX',0)), get_val(ei.get('_displayY',0)),
                          get_val(ei.get('_left',None)), get_val(ei.get('_top',None)))]
        if (t == 'ButtonIcon' and 'close' in nm.lower()) or t == 'Button':
            print(f'  CHAIN to {t} {nm!r}:')
            for i, (ct, cnm, dx, dy, lf, tp) in enumerate(chain2):
                print(f'    {i:2d} {ct:28s} name={cnm[:24]!r} _displayX={dx} _displayY={dy} _left={lf} _top={tp}')
            print(f'    => accumulated @ ({x},{y})')
        for child in node.get('children', []) or []:
            find_btn(child, x, y, chain2)
    find_btn(tc, tx, ty, [('Telecom','telecom',tx,ty,None,None)])

# Итог работы исправленного парсера модалок
from full_parser import parse_modal_info
print('\nparse_modal_info =>', parse_modal_info(d))

