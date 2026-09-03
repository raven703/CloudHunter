import io, sys, json
def gv(v):
    return v.get("int_low32", 0) if isinstance(v, dict) else (v if isinstance(v, (int, float)) else 0)
with open("raw_dump_20260902_184332.json", encoding="utf-8") as f:
    data = json.load(f)
def walk(n, px=0, py=0):
    ei = n.get("dictEntriesOfInterest", {}) or {}
    t = n.get("pythonObjectTypeName", "")
    x = px + gv(ei.get("_displayX")); y = py + gv(ei.get("_displayY"))
    w = gv(ei.get("_displayWidth")); h = gv(ei.get("_displayHeight"))
    name = str(ei.get("_name", ""))
    if t == "Telecom":
        print(f"TELECOM @ ({x},{y}) {w}x{h}")
    if t in ("ButtonIcon",) and "Close" in name:
        print(f"CLOSE {t} name={name!r} @ ({x},{y}) {w}x{h} center=({int(x+w/2)},{int(y+h/2)})")
    txt = str(ei.get("_setText", ""))
    if "Information" in txt:
        print(f"INFO_TITLE @ ({x},{y}) txt={txt!r}")
    for c in n.get("children", []) or []:
        walk(c, x, y)
walk(data)
