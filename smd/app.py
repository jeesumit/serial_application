#!/usr/bin/env python3
"""
Serial Monitor - desktop launcher.

Starts the local backend on 127.0.0.1 and shows the UI in a native window (pywebview;
Microsoft Edge WebView2 on Windows). If no native web view is available it falls back to
your default browser plus a small control window.

  python app.py                  native window
  python app.py --browser        use the default browser instead
  python app.py --selftest       headless end-to-end check (used by the build scripts / CI)
  python app.py --data-dir DIR   where logs + the SQLite database live
Default data dir: %APPDATA%\\SerialMonitor  (Windows) / ~/.local/share/SerialMonitor (Linux)
"""
import argparse, json, os, socket, sys, threading, time, traceback, urllib.request, webbrowser

import server

APP_NAME = "Serial Monitor"
PREFERRED_PORT = 47653        # fixed so the page origin (and its saved quick commands/theme) stays stable


def default_data_dir():
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "SerialMonitor")


def pick_port():
    for port in (PREFERRED_PORT, 0):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError("no free TCP port")


def selftest(url, out_file):
    """Exercise the packaged app end to end: UI, ports, simulator, logging, SQLite."""
    def call(path, body=None):
        req = urllib.request.Request(url + path, json.dumps(body).encode() if body is not None else None)
        return json.loads(urllib.request.urlopen(req, timeout=10).read())
    try:
        html = urllib.request.urlopen(url + "/", timeout=10).read()
        assert b"Serial Monitor" in html, "frontend not served"
        assert any(p["dev"] == "SIMULATOR" for p in call("/api/ports")), "no simulator port"
        assert call("/api/connect", {"port": "SIMULATOR", "baud": 115200, "mode": "text", "fmt": "csv", "db": True}).get("ok")
        time.sleep(1.5)
        assert call("/api/status")["bytes"] > 0, "no data received"
        call("/api/disconnect", {})
        sess = call("/api/db/sessions")
        assert sess and sess[0]["n"] > 5, "sqlite session missing"
        msg, code = "SELFTEST OK: %d records stored" % sess[0]["n"], 0
    except Exception as e:                                   # noqa
        msg, code = "SELFTEST FAILED: %r" % e, 1
    with open(out_file, "w") as f:
        f.write(msg + "\n")
    print(msg)
    return code


def fallback_window(url):
    """Used when no native web view exists: open the browser and keep a tiny control window."""
    webbrowser.open(url)
    try:
        import tkinter as tk
        root = tk.Tk(); root.title(APP_NAME); root.resizable(False, False)
        tk.Label(root, text="%s is running at\n%s\n\nClose this window to quit." % (APP_NAME, url), padx=24, pady=16).pack()
        tk.Button(root, text="Open in browser", command=lambda: webbrowser.open(url)).pack(pady=(0, 14))
        root.mainloop()
    except Exception:
        threading.Event().wait()


def run_window(url, data_dir):
    import webview                                            # lazy: not needed for --selftest/--browser
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True            # Export buttons -> save dialog
    except Exception:
        pass
    webview.create_window(APP_NAME, url, width=1320, height=860, min_size=(980, 600))
    kw = dict(private_mode=False, storage_path=os.path.join(data_dir, "webview"))
    if sys.platform == "win32":
        kw["gui"] = "edgechromium"                            # never fall back to the old IE engine
    webview.start(**kw)


def main():
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--browser", action="store_true")
    ap.add_argument("--data-dir")
    a = ap.parse_args()

    data_dir = a.data_dir or default_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    srv, mon = server.serve("127.0.0.1", pick_port(), os.path.join(data_dir, "logs"),
                            os.path.join(data_dir, "serial_monitor.db"))
    url = "http://127.0.0.1:%d" % srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    code = 0
    try:
        if a.selftest:
            code = selftest(url, os.path.join(data_dir, "selftest.txt"))
        elif a.browser:
            fallback_window(url)
        else:
            try:
                run_window(url, data_dir)
            except Exception:
                with open(os.path.join(data_dir, "app.log"), "a") as f:
                    f.write("%s native window failed:\n%s\n" % (time.ctime(), traceback.format_exc()))
                fallback_window(url)
    finally:
        mon.disconnect()                                      # flush logs / close SQLite session
        srv.shutdown()
    return code


if __name__ == "__main__":
    sys.exit(main())
