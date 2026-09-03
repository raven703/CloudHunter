import json
def gv(v):
    return v.get("int_low32", 0) if isinstance(v, dict) else (v if isinstance(v, (int, float)) else 0)
d = json.load(open("raw_dump_20260902_184332.json", encoding="utf-8"))
Found = []
def walk(n, px=0, py=0):
    ei = n.get("dictEntriesOfInterest", {}) or {}
    t = n.get("pythonObjectTypeName", "")
    x = px + gv(ei.get("_displayX")); y = py + gv(ei.get("_displayY"))
    w = gv(ei.get("_displayWidth")); h = gv(ei.get("_displayHeight"))
    if t == "Telecom":
        Found.append((x, y, w, h, str(ei.get("_name", ""))))
    for c in n.get("children", []) or []:
        walk(c, x, y)
walk(d)
print("Telecom nodes:", Found)
