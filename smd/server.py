#!/usr/bin/env python3
"""
Serial Monitor v3 - non-blocking pipeline, SQLite storage, multi-format logs

Run:     pip install pyserial && python3 server.py        -> http://127.0.0.1:8765
Options: --host H --port N --logdir DIR --db FILE.db --no-browser --decode FILE.bin

Pipeline (why it no longer hangs): reader thread -> memory ring + bounded queue.
A separate writer thread batches the file log and SQLite inserts, so slow disks never
block serial reading, the UI stream or HTTP requests. Streams send batches (<=20/s).

Log formats: csv | json (always-valid array) | txt | bin | off
  bin = "SLG1\\x01" + per record <float64 epoch_s><uint32 delta_us><uint8 dir 0=RX 1=TX 2=SYS><uint16 len><raw>
SQLite schema: sessions(id,started,ended,port,baud,mode,frame,vid,pid)
               records(id,session_id,seq,t,delta,dir,text,raw)    index (session_id,seq)
"""
import argparse, csv, io, json, math, os, queue, random, sqlite3, struct, sys, threading, time, webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None

SIM, MAGIC = "SIMULATOR", b"SLG1\x01"
EXT = {"csv": ".csv", "json": ".json", "txt": ".txt", "bin": ".bin"}
MIME = {"csv": "text/csv", "json": "application/json", "txt": "text/plain", "bin": "application/octet-stream"}
DIRS = {"RX": 0, "TX": 1, "SYS": 2}
CSV_HEAD = b"seq,timestamp,delta_s,dir,data\r\n"
SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY, started REAL, ended REAL, port TEXT, baud INTEGER,
  mode TEXT, frame TEXT, vid TEXT, pid TEXT);
CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  seq INTEGER, t REAL, delta REAL, dir TEXT, text TEXT, raw BLOB);
CREATE INDEX IF NOT EXISTS ix_records ON records(session_id, seq);"""


def clean(buf): return "".join(chr(c) if 32 <= c < 127 else "\\x%02X" % c for c in buf)
def fmt_ts(t): return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S.") + "%03d" % int(t % 1 * 1000)


def to_bytes(fmt, r):
    if fmt == "csv":
        s = io.StringIO(); csv.writer(s).writerow([r["seq"], r["ts"], r["delta"], r["dir"], r["text"]])
        return s.getvalue().encode()
    if fmt == "json":
        return json.dumps({k: r[k] for k in ("seq", "ts", "t", "delta", "dir", "text")}, ensure_ascii=False).encode()
    if fmt == "txt":
        return ("#%06d %s +%.3fs %-3s | %s\n" % (r["seq"], r["ts"], r["delta"], r["dir"], r["text"])).encode()
    raw = r["raw"]
    return struct.pack("<dIBH", r["t"], int(r["delta"] * 1e6), DIRS[r["dir"]], len(raw)) + raw


def build(fmt, recs):
    if fmt == "json": return b"[\n" + b",\n".join(to_bytes(fmt, r) for r in recs) + b"\n]\n"
    return {"csv": CSV_HEAD, "bin": MAGIC}.get(fmt, b"") + b"".join(to_bytes(fmt, r) for r in recs)


class LogWriter:
    def __init__(self, path, fmt):
        self.fmt, self.first = fmt, True
        self.f = open(path, "wb")
        self.f.write({"csv": CSV_HEAD, "bin": MAGIC, "json": b"[\n]\n"}.get(fmt, b"")); self.f.flush()

    def write(self, recs):
        if self.fmt == "json":                       # file stays a valid array after every batch
            self.f.seek(-3, 2)
            parts = []
            for r in recs:
                parts.append((b"" if self.first else b",\n") + to_bytes("json", r)); self.first = False
            self.f.write(b"".join(parts) + b"\n]\n")
        else:
            self.f.write(b"".join(to_bytes(self.fmt, r) for r in recs))
        self.f.flush()

    def close(self): self.f.close()


def decode_bin(path):
    with open(path, "rb") as f:
        assert f.read(5) == MAGIC, "not an SLG1 log"
        n, seq = struct.calcsize("<dIBH"), 0
        while (h := f.read(n)):
            t, d_us, dr, ln = struct.unpack("<dIBH", h); seq += 1
            print("#%06d %s +%.3fs %s | %s" % (seq, fmt_ts(t), d_us / 1e6, ["RX", "TX", "SYS"][dr], clean(f.read(ln))))


class Sink(threading.Thread):
    """Writer thread: file log + SQLite, batched, never blocks the reader."""
    def __init__(self, lw, dbpath, meta):
        super().__init__(daemon=True)
        self.lw, self.dbpath, self.meta = lw, dbpath, meta
        self.q, self.dropped, self.error, self.sid = queue.Queue(200000), 0, "", None
        self.ready, self.quit = threading.Event(), False

    def put(self, r):
        try: self.q.put_nowait(r)
        except queue.Full: self.dropped += 1

    def run(self):
        con = None
        try:
            if self.dbpath:
                os.makedirs(os.path.dirname(os.path.abspath(self.dbpath)), exist_ok=True)
                con = sqlite3.connect(self.dbpath, timeout=5)
                con.execute("PRAGMA journal_mode=WAL"); con.execute("PRAGMA synchronous=NORMAL")
                con.execute("PRAGMA foreign_keys=ON"); con.executescript(SCHEMA)
                self.sid = con.execute("INSERT INTO sessions(started,port,baud,mode,frame,vid,pid) VALUES(?,?,?,?,?,?,?)",
                                       self.meta).lastrowid
                con.commit()
        except Exception as e:
            self.error = "db: %s" % e; con = None
        self.ready.set()
        while not (self.quit and self.q.empty()):
            batch = []
            try:
                x = self.q.get(timeout=0.25)
                while True:
                    batch.append(x)
                    if len(batch) >= 5000: break
                    x = self.q.get_nowait()
            except queue.Empty:
                pass
            if not batch: continue
            try:
                if self.lw: self.lw.write(batch)
                if con:
                    con.executemany("INSERT INTO records(session_id,seq,t,delta,dir,text,raw) VALUES(?,?,?,?,?,?,?)",
                                    [(self.sid, r["seq"], r["t"], r["delta"], r["dir"], r["text"], r["raw"]) for r in batch])
                    con.commit()
            except Exception as e:
                self.error = str(e)
        try:
            if con: con.execute("UPDATE sessions SET ended=? WHERE id=?", (time.time(), self.sid)); con.commit(); con.close()
            if self.lw: self.lw.close()
        except Exception as e:
            self.error = str(e)

    def finish(self):
        self.quit = True; self.join(10)


class Monitor:
    def __init__(self, logdir, dbpath):
        self.logdir, self.dbpath = logdir, dbpath
        self.cv = threading.Condition()
        self.recs, self.base, self.seq, self.epoch, self.last = [], 0, 0, 0, None
        self.stop = threading.Event()
        self.thread = self.ser = self.sink = None
        self.simq = []
        self.cfg, self.connected, self.err, self.bytes, self.logpath = {}, False, "", 0, ""

    def add(self, d, text, stamp=None, raw=None):
        wall, mono = stamp or (time.time(), time.monotonic())
        with self.cv:
            self.seq += 1
            delta = 0.0 if self.last is None else mono - self.last
            self.last = mono
            r = {"seq": self.seq, "t": round(wall, 3), "delta": round(delta, 3), "dir": d, "text": text,
                 "ts": fmt_ts(wall), "raw": raw if raw is not None else text.encode()}
            self.recs.append(r)
            if len(self.recs) > 30000: del self.recs[:5000]; self.base += 5000
            self.cv.notify_all()
        if self.sink: self.sink.put(r)              # non-blocking, outside the lock

    def since(self, last, n=5000):
        i = max(0, last - self.base)
        return self.recs[i:i + n]

    def connect(self, b):
        self.disconnect()
        port, baud, fmt = b["port"], int(b["baud"]), b.get("fmt", "csv")
        mode, frame = b.get("mode", "text"), "%s%s%s" % (b.get("bytesize", 8), b.get("parity", "N"), b.get("stopbits", 1))
        if fmt != "off" and fmt not in EXT: raise RuntimeError("bad log format")
        if port != SIM:
            if serial is None: raise RuntimeError("pyserial missing: pip install pyserial")
            par = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD,
                   "M": serial.PARITY_MARK, "S": serial.PARITY_SPACE}[b.get("parity", "N")]
            sb = {"1": serial.STOPBITS_ONE, "1.5": serial.STOPBITS_ONE_POINT_FIVE, "2": serial.STOPBITS_TWO}[str(b.get("stopbits", 1))]
            self.ser = serial.Serial(port, baud, bytesize=int(b.get("bytesize", 8)), parity=par, stopbits=sb,
                                     timeout=0.05, write_timeout=1)
        lw = None
        if fmt != "off":
            os.makedirs(self.logdir, exist_ok=True)
            self.logpath = os.path.join(self.logdir, time.strftime("serial_%Y%m%d_%H%M%S") + EXT[fmt])
            lw = LogWriter(self.logpath, fmt)
        else:
            self.logpath = ""
        info = next((p for p in PORTS if p["dev"] == port), {})
        self.sink = Sink(lw, self.dbpath if b.get("db") else None,
                         (time.time(), port, baud, mode, frame, info.get("vid", ""), info.get("pid", "")))
        self.sink.start(); self.sink.ready.wait(5)
        with self.cv:
            self.recs, self.base, self.seq, self.last = [], 0, 0, None; self.epoch += 1
        self.cfg = {"port": port, "baud": baud, "mode": mode, "fmt": fmt, "frame": frame, "db": bool(b.get("db"))}
        self.bytes, self.err, self.connected = 0, "", True
        self.stop.clear()
        self.add("SYS", "Connected %s @ %d baud %s (%s mode, log=%s%s)" % (port, baud, frame, mode, fmt, ", sqlite" if b.get("db") else ""))
        self.thread = threading.Thread(target=self.run, daemon=True); self.thread.start()

    def disconnect(self):
        if not self.connected and not self.sink: return
        self.stop.set()
        if self.thread: self.thread.join(2)
        if self.ser:
            try: self.ser.close()
            except Exception: pass
            self.ser = None
        if self.connected: self.add("SYS", "Disconnected")
        self.connected = False
        if self.sink: self.sink.finish(); self.sink = None

    def send(self, text, eol):
        if not self.connected: raise RuntimeError("not connected")
        data = (text + eol).encode()
        if self.ser: self.ser.write(data)
        else: self.simq.append(("ECHO:" + text + "\n").encode())
        self.add("TX", text, raw=data)

    def run(self):
        buf, t0, last_rx, n, nxt = bytearray(), None, 0.0, 0, time.monotonic()
        hexmode = self.cfg["mode"] == "hex"
        try:
            while not self.stop.is_set():
                if self.ser:
                    data = self.ser.read(max(1, min(self.ser.in_waiting, 4096)))
                else:
                    time.sleep(0.04); data = b""
                    while self.simq: data += self.simq.pop(0)
                    if time.monotonic() >= nxt:
                        n += 1
                        data += ("SENSOR,%d,T=%.2f,H=%.1f,P=%.1f\n" % (n, 24 + 2.5 * math.sin(n / 9) + random.random() * .5,
                                 52 + 6 * math.cos(n / 12) + random.random(), 1004 + random.random() * 2)).encode()
                        nxt = time.monotonic() + 0.08
                if data:
                    self.bytes += len(data); last_rx = time.monotonic()
                    if hexmode:
                        self.add("RX", " ".join("%02X" % b for b in data), raw=data); continue
                    for b in data:
                        if b == 10:
                            if buf: self.add("RX", clean(buf), t0, bytes(buf)); buf.clear()
                        elif b != 13:
                            if not buf: t0 = (time.time(), time.monotonic())
                            buf.append(b)
                            if len(buf) >= 4096: self.add("RX", clean(buf) + "  [partial]", t0, bytes(buf)); buf.clear()
                elif buf and time.monotonic() - last_rx > 0.15:
                    self.add("RX", clean(buf) + "  [partial]", t0, bytes(buf)); buf.clear()
        except Exception as e:
            self.err = str(e); self.connected = False
            self.add("SYS", "Error: %s" % e)


# ---------------- port list (cached, scanned in background so HTTP never blocks) ----------------
M = None
PORTS = [{"dev": SIM, "vid": "", "pid": "", "desc": "built-in fake sensor", "mfr": "", "sn": ""}]
BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))   # works inside PyInstaller bundles
STATIC = os.path.join(BASE, "static")


def scan():
    global PORTS
    out = [PORTS[0]]
    if serial:
        try:
            for p in sorted(list_ports.comports(), key=lambda p: (p.vid is None, p.device)):
                out.append({"dev": p.device, "vid": "%04X" % p.vid if p.vid is not None else "",
                            "pid": "%04X" % p.pid if p.pid is not None else "",
                            "desc": p.description or "", "mfr": p.manufacturer or "", "sn": p.serial_number or ""})
        except Exception:
            return
    PORTS = out


def scan_loop():
    while True:
        scan(); time.sleep(3)


def scan_now():
    t = threading.Thread(target=scan, daemon=True); t.start(); t.join(2.5)


# ---------------- SQLite read helpers ----------------
def db_ro():
    p = M.dbpath
    if not os.path.exists(p): raise RuntimeError("database not found: %s" % p)
    con = sqlite3.connect("file:%s?mode=ro" % quote(os.path.abspath(p)), uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def db_query(sql, args=()):
    con = db_ro(); t0 = time.time()
    con.set_progress_handler(lambda: time.time() - t0 > 2, 10000)     # abort runaway queries
    try:
        cur = con.execute(sql, args); rows = cur.fetchmany(501)
        cols = [d[0] for d in cur.description or []]
        trunc = len(rows) > 500
        return cols, [[("0x" + v[:32].hex() + ("…" if len(v) > 32 else "")) if isinstance(v, bytes) else v for v in r] for r in rows[:500]], trunc
    finally:
        con.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def reply(self, body, ctype="application/json", code=200, extra=None):
        if not isinstance(body, bytes): body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(body)

    def attach(self, name, fmt, data):
        self.reply(data, MIME[fmt], extra={"Content-Disposition": 'attachment; filename="%s%s"' % (name, EXT[fmt])})

    def do_GET(self):
        u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/":
                with open(os.path.join(STATIC, "index.html"), "rb") as f: self.reply(f.read(), "text/html; charset=utf-8")
            elif u.path == "/api/ports":
                if q.get("refresh"): scan_now()
                self.reply(PORTS)
            elif u.path == "/api/status":
                s = M.sink
                self.reply({"connected": M.connected, "cfg": M.cfg, "err": M.err, "bytes": M.bytes, "logpath": M.logpath,
                            "dropped": s.dropped if s else 0, "sinkerr": s.error if s else "", "sid": s.sid if s else None,
                            "dbpath": M.dbpath})
            elif u.path == "/api/log":
                fmt = q.get("fmt", M.cfg.get("fmt", "csv"))
                if fmt not in EXT or not M.seq: return self.reply({"error": "no data"}, code=404)
                with M.cv: recs = list(M.recs)
                self.attach("serial_export_" + time.strftime("%Y%m%d_%H%M%S"), fmt, build(fmt, recs))
            elif u.path == "/api/stream":
                self.stream()
            elif u.path == "/api/db/sessions":
                cols, rows, _ = db_query("SELECT s.id,s.started,s.ended,s.port,s.baud,s.mode,s.frame,s.vid,s.pid,"
                                         "(SELECT COUNT(*) FROM records r WHERE r.session_id=s.id) AS n FROM sessions s ORDER BY s.id DESC LIMIT 200")
                self.reply([dict(zip(cols, r)) for r in rows])
            elif u.path == "/api/db/records":
                cols, rows, _ = db_query("SELECT seq,t,delta,dir,text FROM records WHERE session_id=? ORDER BY seq LIMIT ? OFFSET ?",
                                         (int(q["session"]), min(int(q.get("limit", 50000)), 200000), int(q.get("offset", 0))))
                self.reply({"r": [{"seq": a, "t": t, "ts": fmt_ts(t), "delta": d, "dir": dr, "text": tx} for a, t, d, dr, tx in rows]})
            elif u.path == "/api/db/export":
                con = db_ro()
                recs = [dict(r, ts=fmt_ts(r["t"])) for r in con.execute(
                    "SELECT seq,t,delta,dir,text,raw FROM records WHERE session_id=? ORDER BY seq", (int(q["session"]),))]
                con.close()
                self.attach("session_%s" % q["session"], q.get("fmt", "csv"), build(q.get("fmt", "csv"), recs))
            else:
                self.reply({"error": "not found"}, code=404)
        except OSError:
            pass
        except Exception as e:
            self.reply({"error": str(e)})

    def stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        last, epoch = 0, M.epoch
        try:
            while True:
                with M.cv:
                    M.cv.wait_for(lambda: M.seq > last or M.epoch != epoch, 10)
                    chunk = M.since(last) if M.epoch == epoch else []
                if M.epoch != epoch: return           # new session: client reopens
                if chunk:
                    self.wfile.write(("data: %s\n\n" % json.dumps({"r": [{k: v for k, v in r.items() if k != "raw"} for r in chunk]})).encode())
                    last = chunk[-1]["seq"]
                    self.wfile.flush(); time.sleep(0.05)   # coalesce: max ~20 messages/s
                else:
                    self.wfile.write(b": keepalive\n\n"); self.wfile.flush()
        except OSError:
            return

    def do_POST(self):
        try:
            b = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/api/connect": M.connect(b)
            elif self.path == "/api/disconnect": M.disconnect()
            elif self.path == "/api/send": M.send(b["data"], b.get("eol", "\n"))
            elif self.path == "/api/db/path": M.dbpath = b["path"]
            elif self.path == "/api/db/query":
                cols, rows, trunc = db_query(b["sql"])
                return self.reply({"cols": cols, "rows": rows, "truncated": trunc})
            elif self.path == "/api/db/delete":
                con = sqlite3.connect(M.dbpath, timeout=5); con.execute("PRAGMA foreign_keys=ON")
                con.execute("DELETE FROM records WHERE session_id=?", (int(b["session"]),))
                con.execute("DELETE FROM sessions WHERE id=?", (int(b["session"]),)); con.commit(); con.close()
            else:
                return self.reply({"error": "not found"}, code=404)
            self.reply({"ok": True})
        except Exception as e:
            self.reply({"error": str(e)})


def serve(host, port, logdir, dbpath):
    """Create the monitor + HTTP server (not started). Used by the desktop launcher."""
    global M
    M = Monitor(logdir, dbpath)
    threading.Thread(target=scan_loop, daemon=True).start()
    srv = ThreadingHTTPServer((host, port), Handler); srv.daemon_threads = True
    return srv, M


def main():
    global M
    ap = argparse.ArgumentParser(description="Serial Monitor v3")
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--logdir", default="logs"); ap.add_argument("--db", default="logs/serial_monitor.db")
    ap.add_argument("--no-browser", action="store_true"); ap.add_argument("--decode", metavar="FILE.bin")
    a = ap.parse_args()
    if a.decode: return decode_bin(a.decode)
    M = Monitor(a.logdir, a.db)
    threading.Thread(target=scan_loop, daemon=True).start()
    srv = ThreadingHTTPServer((a.host, a.port), Handler); srv.daemon_threads = True
    url = "http://%s:%d" % (a.host, a.port)
    print("Serial Monitor running at", url, "(Ctrl-C to quit)  db:", a.db)
    if serial is None: print("Note: pyserial not installed - only SIMULATOR available")
    if not a.no_browser: threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
    finally: M.disconnect()


if __name__ == "__main__":
    main()
