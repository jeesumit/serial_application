# PyInstaller spec - one-folder, windowed build.   pyinstaller --noconfirm serial_monitor.spec
import sys
from PyInstaller.utils.hooks import collect_submodules

win = sys.platform == "win32"
hidden = collect_submodules("serial.tools")
if win:
    hidden += ["webview.platforms.edgechromium", "webview.platforms.winforms"]

a = Analysis(["app.py"], pathex=["."],
             datas=[("static", "static"), ("assets/icon.ico", "assets"), ("assets/icon.png", "assets")],
             hiddenimports=hidden,
             excludes=["matplotlib", "numpy", "scipy", "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython", "pytest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SerialMonitor", console=False,
          icon="assets/icon.ico" if win else None)
coll = COLLECT(exe, a.binaries, a.datas, name="SerialMonitor")
