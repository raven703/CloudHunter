with open("test_bot_integration.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "kd and kd[0]" in line and "'w'" in line and "orbit" in line.lower():
        lines[i] = "assert kd and kd[0] == ('w',), f\"orbit: W must be held, got {kd}\" \n"
        break

with open("test_bot_integration.py", "w", encoding="utf-8") as f:
    f.writelines(lines)
print("Fixed")
