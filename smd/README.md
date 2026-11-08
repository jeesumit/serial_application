# Serial Monitor — Windows desktop app (v1.0.0)

Native-window serial monitor: live Print/Plot, 4 log formats, SQLite storage, VID/PID port info.
The app starts a local backend on 127.0.0.1 and shows it in its own window (Microsoft Edge WebView2).

## Get the installer (pick one)

### A) GitHub Actions — no tools needed on your PC
1. Create a GitHub repository and upload this folder's contents (keep `.github/workflows/`).
2. Open **Actions → Build Windows installer → Run workflow** (or push a tag like `v1.0.0`).
3. Download the **SerialMonitor-windows** artifact: `SerialMonitor-Setup-1.0.0.exe` + a portable zip.
   The workflow runs the packaged app's self-test before building the installer. Tags also create a GitHub Release.

### B) Build on your own Windows PC
Install **Python 3.11** (tick "Add to PATH") and, optionally, **Inno Setup 6** (https://jrsoftware.org/isinfo.php), then:

    build_windows.bat

Output in `dist\`: `SerialMonitor\SerialMonitor.exe` (portable folder), `SerialMonitor-1.0.0-portable.zip`,
and — if Inno Setup is installed — `SerialMonitor-Setup-1.0.0.exe`.

## Install and run
Run the setup, keep "desktop shortcut" ticked, Finish. Per-user install by default (no admin prompt).
The installer adds the WebView2 runtime if your PC lacks it (Windows 11 and current Windows 10 already have it).

* Your data lives in `%APPDATA%\SerialMonitor\` — `logs\` (CSV/JSON/TXT/BIN) and `serial_monitor.db` (SQLite).
  Uninstalling keeps this folder; delete it by hand to remove your data.
* USB-serial drivers (CP210x, FTDI, CH340, Arduino) are separate from this app. If a device shows no COM port in Device Manager, install its driver.
* The simulator port works without any hardware.

## Development / other platforms
    pip install -r requirements-desktop.txt
    python app.py              # native window
    python app.py --browser    # default browser instead
    python app.py --selftest   # headless end-to-end check
    python server.py           # plain web server mode (http://127.0.0.1:8765)
Linux native window needs GTK or Qt for pywebview (e.g. `sudo apt install python3-gi gir1.2-webkit2-4.1`); otherwise it opens your browser.

## Troubleshooting
* **"Windows protected your PC" (SmartScreen)**: the installer is unsigned. Click *More info → Run anyway*, or sign it with a code-signing certificate.
* **Antivirus flags the exe**: PyInstaller apps are sometimes false positives; the folder build used here is the least likely to be flagged.
* **Window does not open**: the app falls back to your browser; details are in `%APPDATA%\SerialMonitor\app.log`.
* **COM port "access denied"**: another program (Arduino IDE, PuTTY) has the port open — close it first.

## Project layout
    app.py                 desktop launcher (native window, fallback, --selftest)
    server.py              backend: serial reader, writer thread, SQLite, HTTP/SSE API
    static/index.html      frontend
    serial_monitor.spec    PyInstaller build        installer.iss   Inno Setup installer
    build_windows.bat      one-click local build    .github/workflows/build-windows.yml   CI build
    tools/make_icon.py     regenerates assets/icon.*   VERSION   app/installer version
