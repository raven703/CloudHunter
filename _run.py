import sys, traceback

# Сохраняем настоящие stdout/stderr — чтобы перехват в main.py их не подменил
_real_stdout = sys.__stdout__
_real_stderr = sys.__stderr__

try:
    import main
    app = main.EveApp("eveui.exe")
    app.run()
except BaseException:
    _real_stderr.write("\n=== EXCEPTION IN MAIN ===\n")
    traceback.print_exc(file=_real_stderr)
    _real_stderr.flush()
    raise