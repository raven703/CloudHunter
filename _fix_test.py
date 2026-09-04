import sys

with open("test_bot_integration.py", "r", encoding="utf-8") as f:
    content = f.read()

# Fix test 2b: keyboard.press instead of pag.keyDown
old1 = """kd = [c.args for c in pag.keyDown.call_args_list if c.args]
print(f"2b) orbit action     -> step={bot._combat_step}, moves={moves}, keys={kd}")
assert bot._combat_step == 'ORBIT'
assert kd and kd[0] == ('w',), f"orbit: W must be held for W+LMB, got {kd}\" """

new1 = """kd = [c.args for c in pag.keyDown.call_args_list if c.args]
kbd = [c.args for c in _keyboard.press.call_args_list if c.args] if _HAS_KEYBOARD else []
print(f"2b) orbit action     -> step={bot._combat_step}, moves={moves}, keys={kd}, kbd={kbd}")
assert bot._combat_step == 'ORBIT'
# W через keyboard (скан-код) или pyautogui.keyDown
assert (kd and kd[0] == ('w',)) or (kbd and 'w' in str(kbd)), f"orbit: W must be pressed, got kd={kd} kbd={kbd}\" """

if old1 in content:
    content = content.replace(old1, new1)
    print("Fixed test 2b")
else:
    print("old1 not found")
    # покажу что есть
    idx = content.find("2b) orbit action")
    if idx >= 0:
        print(repr(content[idx:idx+200]))

with open("test_bot_integration.py", "w", encoding="utf-8") as f:
    f.write(content)
