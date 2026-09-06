#!/usr/bin/env python3
"""omaclackd: keyboard and mouse sound daemon for the Omaclack Omarchy plugin.

Reads /dev/input/event* directly and spawns one short-lived `pw-play` per key
press or release on a per-key Opus file. pw-play auto-connects to PipeWire,
decodes via libsndfile and exits when the sample ends; nothing stays resident
but this process sleeping in select() and, while a room preset is active, one
`pipewire` filter-chain child that suspends when idle.

Stdlib only. No logging, no network, nothing written to disk except the
generated room configs under $XDG_RUNTIME_DIR. Child of omarchy-shell; the
stdin pipe is both the control channel and the lifeline.
"""

import collections
import fcntl
import json
import math
import os
import random
import select
import signal
import socket
import struct
import subprocess
import sys
import time
import wave

# ---------------------------------------------------------------- constants

EV_KEY = 0x01
KEY_MAX_KEYBOARD = 0x100
BTN_MOUSE_MIN, BTN_MOUSE_MAX = 0x110, 0x117

DEBOUNCE_MS = 30.0
RESCAN_S = 3.0
MAX_CONCURRENT = 24  # cap on simultaneous pw-play processes
SAMPLE_EXT = (".opus", ".ogg", ".flac", ".wav")
RELEASE_GAIN = 0.7   # release samples relative to the press volume
STATS_WINDOW_S = 300.0

INPUT_EVENT = struct.Struct("llHHi")
EVIOCGNAME = 0x81004506  # _IOC(_IOC_READ, 'E', 0x06, 256)

HERE = os.path.dirname(os.path.abspath(__file__))
SOUNDS_DIR = os.path.join(os.path.dirname(HERE), "sounds")
MOUSE_DIR = os.path.join(SOUNDS_DIR, "mouse")
USER_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        "omarchy", "omaclack", "packs")
USER_MOUSE_DIR = os.path.join(USER_DIR, "mouse")
ROOM_SINK = "omaclack.room"


def runtime_dir():
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(base, "omaclack")


# ---------------------------------------------------------------- packs

def _default_file(path):
    for ext in SAMPLE_EXT:
        f = os.path.join(path, "default" + ext)
        if os.path.isfile(f):
            return f
    return None


def find_pack_dir(name, roots):
    for root in roots:
        d = os.path.join(root, name)
        if _default_file(d):
            return d
    return None


def list_packs(roots):
    """Pack ids across the shipped and user roots; shipped wins on collision."""
    seen = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for d in sorted(os.listdir(root)):
            if d not in seen and _default_file(os.path.join(root, d)):
                seen.append(d)
    return sorted(seen)


def pack_meta(name, roots):
    """pack.json contents plus id; name falls back to the folder name."""
    d = find_pack_dir(name, roots) or ""
    meta = {}
    try:
        with open(os.path.join(d, "pack.json")) as f:
            meta = json.load(f)
    except (OSError, ValueError):
        pass
    return {"id": name, "name": str(meta.get("name") or name),
            "credit": str(meta.get("credit") or ""), "source": str(meta.get("source") or ""),
            "release": str(meta.get("release") or ""),
            "user": bool(d and not d.startswith(SOUNDS_DIR))}


class SampleSet:
    """<code>.<ext> wins, then a keys map, then default.<ext>."""

    def __init__(self, path, keys):
        self.default = _default_file(path)
        self.files = {}
        if self.default is None:
            return
        for fn in os.listdir(path):
            stem, ext = os.path.splitext(fn)
            if ext in SAMPLE_EXT and stem.isdigit():
                self.files[int(stem)] = os.path.join(path, fn)
        for code, fn in (keys or {}).items():
            f = os.path.join(path, os.path.basename(str(fn)))
            if str(code).isdigit() and int(code) not in self.files and os.path.isfile(f):
                self.files[int(code)] = f

    def sample_for(self, code):
        return self.files.get(code, self.default)


class Pack:
    """A press SampleSet and an optional release SampleSet in up/."""

    def __init__(self, name, roots):
        self.name = name
        path = find_pack_dir(name, roots)
        if path is None:
            raise FileNotFoundError(os.path.join(roots[0], name, "default.opus"))
        try:
            with open(os.path.join(path, "pack.json")) as f:
                keys = json.load(f).get("keys") or {}
        except (OSError, ValueError):
            keys = {}
        self.press = SampleSet(path, keys)
        up = SampleSet(os.path.join(path, "up"), keys)
        self.release = up if up.default else None

    def sample_for(self, code, down=True):
        if down or self.release is None:
            return self.press.sample_for(code) if down else None
        return self.release.sample_for(code)


# ---------------------------------------------------------------- playback

class OneShotPlayer:
    """One pw-play per sample; gain is passed as --volume so no audio touches Python."""

    def __init__(self):
        self.muted = False
        self.target = None      # PipeWire node name, set while a room is active
        self.live = []          # in-flight pw-play processes
        self.stream_ok = True   # last spawn attempt succeeded

    def reap(self):
        self.live = [p for p in self.live if p.poll() is None]

    def play(self, path, volume=1.0):
        g = 0.0 if self.muted else max(0.0, min(1.0, volume))
        if g == 0.0 or not path:
            return
        self.reap()
        if len(self.live) >= MAX_CONCURRENT:
            return
        cmd = ["pw-play", "--volume", "%.3f" % g]
        if self.target:
            cmd += ["--target", self.target]
        self._spawn(cmd + [path])

    def _spawn(self, cmd):
        try:
            self.live.append(subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            self.stream_ok = True
        except OSError:
            self.stream_ok = False


# ---------------------------------------------------------------- rooms

# Each preset: an impulse response synthesised at start (decay seconds,
# early-reflection delays in ms, one-pole lowpass Hz, wet level) plus a
# low shelf and lowpass in the filter chain in front of the convolver.
ROOMS = {
    "desk": dict(desc="On the desk", decay=0.022, refl=(1.3, 2.9), lp=9000, wet=0.35,
                 shelf=(180.0, 1.5), cut=14000.0),
    "tray": dict(desc="Deep aluminium tray", decay=0.055, refl=(2.1, 4.7, 7.9), lp=4200, wet=0.55,
                 shelf=(140.0, 4.0), cut=6500.0),
    "wood": dict(desc="Wooden desk", decay=0.040, refl=(3.0, 6.5, 11.0), lp=6000, wet=0.45,
                 shelf=(200.0, 3.0), cut=9000.0),
    "wall": dict(desc="Through a wall", decay=0.120, refl=(6.0, 13.0, 21.0, 34.0), lp=1100, wet=0.8,
                 shelf=(120.0, 2.0), cut=1400.0),
}


def synth_ir(path, decay, refl, lp, wet, rate=48000):
    """Direct spike + early reflections + decaying noise tail, one-pole lowpassed."""
    rnd = random.Random(7)
    n = int(rate * max(decay * 4, 0.03))
    out = [0.0] * n
    out[0] = 1.0
    for i, ms in enumerate(refl):
        k = int(rate * ms / 1000.0)
        if k < n:
            out[k] += 0.45 * wet / (i + 1)
    for k in range(1, n):
        out[k] += wet * 0.6 * rnd.uniform(-1, 1) * math.exp(-k / (rate * decay))
    a = math.exp(-2 * math.pi * lp / rate)
    y = 0.0
    for k in range(n):
        y = (1 - a) * out[k] + a * y
        out[k] = y
    peak = max(abs(v) for v in out) or 1.0
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(v / peak * 30000)) for v in out))


ROOM_CONF = """context.properties = {{ log.level = 0 }}
context.spa-libs = {{ audio.convert.* = audioconvert/libspa-audioconvert support.* = support/libspa-support }}
context.modules = [
  {{ name = libpipewire-module-protocol-native }}
  {{ name = libpipewire-module-client-node }}
  {{ name = libpipewire-module-adapter }}
  {{ name = libpipewire-module-filter-chain
    args = {{
      node.description = "Omaclack room"
      media.name = "Omaclack room"
      filter.graph = {{
        nodes = [
          {{ type = builtin name = shelf label = bq_lowshelf control = {{ "Freq" = {shelf_f} "Q" = 0.8 "Gain" = {shelf_g} }} }}
          {{ type = builtin name = cut label = bq_lowpass control = {{ "Freq" = {cut} "Q" = 0.7 }} }}
          {{ type = builtin name = conv label = convolver config = {{ filename = "{ir}" gain = 1.0 }} }}
        ]
        links = [
          {{ output = "shelf:Out" input = "cut:In" }}
          {{ output = "cut:Out" input = "conv:In" }}
        ]
        inputs = [ "shelf:In" ]
        outputs = [ "conv:Out" ]
      }}
      audio.channels = 2
      audio.position = [ FL FR ]
      capture.props = {{ node.name = "{sink}" media.class = Audio/Sink node.description = "Omaclack room" }}
      playback.props = {{ node.name = "{sink}.out" node.passive = true }}
    }}
  }}
]
"""


class Room:
    """Owns the `pipewire -c` child that provides the room sink."""

    def __init__(self, player):
        self.player = player
        self.name = "none"
        self.proc = None

    def set(self, name):
        name = name if name in ROOMS else "none"
        if name == self.name and (name == "none" or (self.proc and self.proc.poll() is None)):
            return self.name
        self.stop()
        if name != "none":
            self.start(name)
        self.name = name
        return name

    def start(self, name):
        r = ROOMS[name]
        d = runtime_dir()
        os.makedirs(d, mode=0o700, exist_ok=True)
        ir = os.path.join(d, "room-%s.wav" % name)
        conf = os.path.join(d, "room-%s.conf" % name)
        if not os.path.exists(ir):
            synth_ir(ir, r["decay"], r["refl"], r["lp"], r["wet"])
        with open(conf, "w") as f:
            f.write(ROOM_CONF.format(shelf_f=r["shelf"][0], shelf_g=r["shelf"][1], cut=r["cut"],
                                     ir=ir, sink=ROOM_SINK))
        try:
            self.proc = subprocess.Popen(["pipewire", "-c", conf], stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.player.target = ROOM_SINK
        except OSError:
            self.proc = None
            self.player.target = None

    def stop(self):
        self.player.target = None
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


# ---------------------------------------------------------------- key filtering

class KeyFilter:
    """Drops repeats of the same key inside the debounce window. Every key
    sounds on its own, modifiers included."""

    def __init__(self, debounce_ms=DEBOUNCE_MS):
        self.debounce_ms = debounce_ms
        self.last = {}

    def on_press(self, code, now):
        t = self.last.get(code)
        if t is not None and now - t < self.debounce_ms:
            return False
        self.last[code] = now
        return True


# ---------------------------------------------------------------- stats

class Stats:
    """In-memory typing stats for the panel. Dies with the process."""

    def __init__(self):
        self.times = collections.deque()      # press timestamps, last 5 min
        self.counts = collections.Counter()   # per keycode, session
        self.total = 0

    def press(self, code, now):
        self.times.append(now)
        self.counts[code] += 1
        self.total += 1
        cutoff = now - STATS_WINDOW_S
        while self.times and self.times[0] < cutoff:
            self.times.popleft()

    def report(self, now):
        recent = [t for t in self.times if t >= now - 60.0]
        buckets = [0] * 10   # inter-key intervals in 60 ms steps, last bucket open
        for a, b in zip(self.times, list(self.times)[1:]):
            buckets[min(9, int((b - a) * 1000 // 60))] += 1
        return {"ok": True, "kpm": len(recent), "total": self.total,
                "window": len(self.times), "rhythm": buckets,
                "top": self.counts.most_common(5)}


# ---------------------------------------------------------------- controller

class Controller:
    def __init__(self, player, pack_roots=(SOUNDS_DIR, USER_DIR), mouse_roots=(MOUSE_DIR, USER_MOUSE_DIR)):
        self.player = player
        self.pack_roots = list(pack_roots)
        self.mouse_roots = list(mouse_roots)
        self.room = Room(player)
        self.stats = Stats()
        self.pack = None
        self.mouse_pack = None
        self.mouse_enabled = True
        self.enabled = True
        self.velocity = True
        self.release_on = True
        self.volume = 0.7        # keyboard
        self.mouse_volume = 0.7
        self.last_press = None
        self.devices = {}        # name -> presses seen
        first = first_or_none(list_packs(self.pack_roots), "mx-blue")
        if first:
            self.load_pack(first)
        first = first_or_none(list_packs(self.mouse_roots), "logitech")
        if first:
            self.load_mouse_pack(first)

    def load_pack(self, name):
        self.pack = Pack(name, self.pack_roots)
        return self.pack.name

    def load_mouse_pack(self, name):
        self.mouse_pack = Pack(name, self.mouse_roots)
        return self.mouse_pack.name

    def status(self):
        return {
            "ok": True,
            "pack": self.pack.name if self.pack else None,
            "packs": [pack_meta(p, self.pack_roots) for p in list_packs(self.pack_roots)],
            "mouse_pack": self.mouse_pack.name if self.mouse_pack else None,
            "mouse_packs": [pack_meta(p, self.mouse_roots) for p in list_packs(self.mouse_roots)],
            "volume": int(round(self.volume * 100)),
            "mouse_volume": int(round(self.mouse_volume * 100)),
            "muted": self.player.muted,
            "enabled": self.enabled,
            "mouse": self.mouse_enabled,
            "velocity": self.velocity,
            "release": self.release_on,
            "room": self.room.name,
            "rooms": [{"id": k, "name": v["desc"]} for k, v in ROOMS.items()],
            "devices": sorted(self.devices, key=lambda n: -self.devices[n]),
            "user_dir": USER_DIR,
            "stream": self.player.stream_ok,
        }

    def velocity_gain(self, now):
        """0.8 for unhurried keys, rising to 1.0 when the previous key was <100 ms ago."""
        if not self.velocity or self.last_press is None:
            return 1.0
        dt = now - self.last_press
        return 0.8 + 0.2 * max(0.0, min(1.0, (0.5 - dt) / 0.4))

    def play(self, code, t0=None, down=True, device=None):
        if not self.enabled:
            return None
        now = t0 if t0 is not None else time.monotonic()
        pack, volume = self.pack, self.volume
        if code >= KEY_MAX_KEYBOARD:
            if not self.mouse_enabled:
                return None
            pack, volume = self.mouse_pack or pack, self.mouse_volume
        if pack is None:
            return None
        if down:
            if code < KEY_MAX_KEYBOARD:
                volume *= self.velocity_gain(now)
                self.last_press = now
                self.stats.press(code, now)
                if device:
                    self.devices[device] = self.devices.get(device, 0) + 1
        else:
            if not self.release_on:
                return None
            volume *= RELEASE_GAIN
        path = pack.sample_for(code, down)
        if path is None:
            return None
        self.player.play(path, volume)
        t1 = time.monotonic()
        return round((t1 - now) * 1000.0, 2)

    def handle(self, msg):
        cmd = msg.get("cmd")
        try:
            if cmd == "play":
                t0 = time.monotonic()
                lat = self.play(int(msg.get("key", 0)), t0, msg.get("down", True) is not False)
                return {"ok": lat is not None, "latency_ms": lat}
            if cmd == "load":
                return {"ok": True, "pack": self.load_pack(str(msg.get("pack", "")))}
            if cmd == "mousepack":
                return {"ok": True, "mouse_pack": self.load_mouse_pack(str(msg.get("pack", "")))}
            if cmd == "volume":
                v = max(0, min(100, int(msg.get("value", 70))))
                self.volume = v / 100.0
                return {"ok": True, "volume": v}
            if cmd == "mousevolume":
                v = max(0, min(100, int(msg.get("value", 70))))
                self.mouse_volume = v / 100.0
                return {"ok": True, "mouse_volume": v}
            if cmd == "mute":
                self.player.muted = bool(msg.get("toggle", True))
                return {"ok": True, "muted": self.player.muted}
            if cmd == "enable":
                self.enabled = bool(msg.get("value", True))
                return {"ok": True, "enabled": self.enabled}
            if cmd == "mouse":
                self.mouse_enabled = bool(msg.get("value", True))
                return {"ok": True, "mouse": self.mouse_enabled}
            if cmd == "velocity":
                self.velocity = bool(msg.get("value", True))
                return {"ok": True, "velocity": self.velocity}
            if cmd == "release":
                self.release_on = bool(msg.get("value", True))
                return {"ok": True, "release": self.release_on}
            if cmd == "room":
                return {"ok": True, "room": self.room.set(str(msg.get("value", "none")))}
            if cmd == "stats":
                return self.stats.report(time.monotonic())
            if cmd == "status":
                return self.status()
            if cmd == "theme":
                # Omarchy theme-set hook: confirm audibly and let the shell react.
                self.play(28, time.monotonic(), True)
                return {"ok": True, "evt": "theme", "slug": str(msg.get("slug", ""))}
            if cmd == "ping":
                return {"ok": True, "pong": msg.get("t")}
            if cmd == "quit":
                return {"ok": True, "quit": True}
            return {"ok": False, "error": "unknown cmd"}
        except (FileNotFoundError, ValueError) as e:
            return {"ok": False, "error": str(e)}

    def close(self):
        self.room.stop()


def first_or_none(names, preferred):
    return preferred if preferred in names else (names[0] if names else None)


# ---------------------------------------------------------------- evdev

class InputReader:
    def __init__(self):
        self.fds = {}       # fd -> path
        self.names = {}     # fd -> device name
        self.last_scan = 0.0
        self.denied = False

    def scan(self, now):
        if now - self.last_scan < RESCAN_S:
            return
        self.last_scan = now
        try:
            names = os.listdir("/dev/input")
        except OSError:
            return
        open_paths = set(self.fds.values())
        self.denied = False
        for n in names:
            if not n.startswith("event"):
                continue
            p = "/dev/input/" + n
            if p in open_paths:
                continue
            try:
                fd = os.open(p, os.O_RDONLY | os.O_NONBLOCK)
                self.fds[fd] = p
                self.names[fd] = self.device_name(fd)
            except PermissionError:
                self.denied = True
            except OSError:
                pass

    @staticmethod
    def device_name(fd):
        buf = bytearray(256)
        try:
            fcntl.ioctl(fd, EVIOCGNAME, buf)
            return buf.split(b"\0", 1)[0].decode("utf-8", "replace").strip()
        except OSError:
            return ""

    def read_events(self, fd):
        """Yield (code, down) for key presses and releases."""
        try:
            buf = os.read(fd, INPUT_EVENT.size * 64)
        except BlockingIOError:
            return
        except OSError:
            self.close(fd)
            return
        if not buf:
            self.close(fd)
            return
        for off in range(0, len(buf) - INPUT_EVENT.size + 1, INPUT_EVENT.size):
            _, _, etype, code, value = INPUT_EVENT.unpack_from(buf, off)
            if etype != EV_KEY or value not in (0, 1):
                continue
            if code < KEY_MAX_KEYBOARD or BTN_MOUSE_MIN <= code <= BTN_MOUSE_MAX:
                yield code, value == 1

    def read_presses(self, fd):
        for code, down in self.read_events(fd):
            if down:
                yield code

    def close(self, fd):
        self.fds.pop(fd, None)
        self.names.pop(fd, None)
        try:
            os.close(fd)
        except OSError:
            pass


# ---------------------------------------------------------------- server

class LineChannel:
    """Newline-delimited JSON over a pair of fds."""

    def __init__(self, rfd, wfd):
        self.rfd, self.wfd = rfd, wfd
        self.rbuf = b""
        self.eof = False

    def read_messages(self):
        try:
            data = os.read(self.rfd, 65536)
        except BlockingIOError:
            return []
        except OSError:
            data = b""
        if not data:
            self.eof = True
            return []
        self.rbuf += data
        out = []
        while b"\n" in self.rbuf:
            line, self.rbuf = self.rbuf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                out.append({"cmd": "__bad__"})
        return out

    def send(self, obj):
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        try:
            while data:
                n = os.write(self.wfd, data)
                data = data[n:]
        except OSError:
            self.eof = True


class CtlServer:
    """Unix socket for the CLI, tests and hooks. One client at a time."""

    def __init__(self, path):
        self.path = path
        self.lockfd = None
        self.srv = None
        self.client = None
        self.chan = None

    def start(self):
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        self.lockfd = os.open(self.path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            sys.exit(2)
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        os.chmod(self.path, 0o600)
        self.srv.listen(1)
        self.srv.setblocking(False)

    def accept(self):
        try:
            c, _ = self.srv.accept()
        except (BlockingIOError, OSError):
            return
        self.drop_client()
        c.setblocking(False)
        self.client = c
        self.chan = LineChannel(c.fileno(), c.fileno())

    def read_messages(self):
        msgs = self.chan.read_messages()
        if self.chan.eof:
            self.drop_client()
        return msgs

    def send(self, obj):
        if self.chan:
            self.chan.send(obj)
            if self.chan.eof:
                self.drop_client()

    def drop_client(self):
        if self.client:
            try:
                self.client.close()
            except OSError:
                pass
        self.client = None
        self.chan = None

    def close(self):
        self.drop_client()
        if self.srv:
            self.srv.close()
        try:
            os.unlink(self.path)
        except OSError:
            pass


# ---------------------------------------------------------------- main loop

def main(argv):
    sock_path = os.path.join(runtime_dir(), "ctl.sock")
    for a in argv[1:]:
        if a.startswith("--socket="):
            sock_path = a.split("=", 1)[1]

    player = OneShotPlayer()
    ctl = Controller(player)
    keys = KeyFilter()
    inputs = InputReader()
    server = CtlServer(sock_path)
    server.start()

    running = True

    def on_signal(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGHUP, on_signal)

    # When stdin is a pipe (spawned by omarchy-shell) it doubles as the control
    # channel: JSON lines in on stdin, replies and events out on stdout. EOF on
    # stdin means the shell is gone, so we exit with it.
    pipe = None
    try:
        import stat as _stat
        if _stat.S_ISFIFO(os.fstat(0).st_mode):
            pipe = LineChannel(0, os.dup(1))
            sys.stdout = sys.stderr
            pipe.send({"evt": "hello", **ctl.status(), "denied": inputs.denied})
    except OSError:
        pass

    def emit(obj):
        server.send(obj)
        if pipe is not None:
            pipe.send(obj)

    def dispatch(chan, msgs):
        nonlocal running
        for msg in msgs:
            resp = ctl.handle(msg)
            resp["id"] = msg.get("id")
            chan.send(resp)
            if resp.get("evt") == "theme":
                emit({"evt": "theme", "slug": resp["slug"]})
            if resp.get("quit"):
                running = False

    while running:
        now = time.monotonic()
        inputs.scan(now)
        rl = list(inputs.fds) + [server.srv]
        if server.client:
            rl.append(server.client)
        if pipe is not None:
            rl.append(pipe.rfd)
        try:
            ready, _, _ = select.select(rl, [], [], RESCAN_S)
        except (InterruptedError, OSError):
            continue
        now = time.monotonic()
        now_ms = now * 1000.0
        for fd in ready:
            if fd is server.srv:
                server.accept()
                server.send({"evt": "hello", **ctl.status(), "denied": inputs.denied})
            elif server.client is not None and fd is server.client:
                dispatch(server, server.read_messages())
            elif pipe is not None and fd == pipe.rfd:
                msgs = pipe.read_messages()
                if pipe.eof:
                    running = False
                dispatch(pipe, msgs)
            else:
                name = inputs.names.get(fd, "")
                for code, down in inputs.read_events(fd):
                    if down and not keys.on_press(code, now_ms):
                        continue
                    lat = ctl.play(code, now, down, name if down else None)
                    if lat is not None and down:
                        emit({"evt": "key", "key": code, "latency_ms": lat})

    ctl.close()
    server.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
