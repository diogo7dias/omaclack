import importlib.machinery
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAEMON = os.path.join(ROOT, "bin", "omaclackd.py")
if not os.path.exists(DAEMON):
    DAEMON = os.path.join(ROOT, "bin", "omaclackd")
SOUNDS = os.path.join(ROOT, "sounds")
# Process-level tests run against this command; default is the Python daemon,
# OMACLACKD_BIN=bin/omaclackd-x86_64 runs them against the Rust build instead.
DAEMON_CMD = [os.environ["OMACLACKD_BIN"]] if os.environ.get("OMACLACKD_BIN") else [sys.executable, DAEMON]


def load_daemon():
    loader = importlib.machinery.SourceFileLoader("omaclackd", DAEMON)
    spec = importlib.util.spec_from_loader("omaclackd", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


D = load_daemon()


class FakePlayer:
    def __init__(self):
        self.muted = False
        self.stream_ok = True
        self.played = []
        self.volumes = []

    def play(self, path, volume=1.0):
        self.played.append(path)
        self.volumes.append(volume)


class OneShotPlayerTest(unittest.TestCase):
    """Exercise OneShotPlayer without spawning pw-play by stubbing _spawn."""

    def setUp(self):
        self.player = D.OneShotPlayer()
        self.sent = []
        self.player._spawn = lambda cmd: self.sent.append(cmd)

    def test_passes_gain_and_path_to_pw_play(self):
        self.player.play("/x/30.opus", 0.5)
        self.assertEqual(self.sent, [["pw-play", "--volume", "0.500", "/x/30.opus"]])

    def test_muted_or_silent_or_empty_plays_nothing(self):
        self.player.muted = True
        self.player.play("/x/30.opus", 1.0)
        self.player.muted = False
        self.player.play("/x/30.opus", 0.0)
        self.player.play(None, 1.0)
        self.assertEqual(self.sent, [])

    def test_concurrency_cap_counts_only_running_children(self):
        class Live:
            def __init__(self, rc): self.rc = rc
            def poll(self): return self.rc
        self.player.live = [Live(None)] * D.MAX_CONCURRENT
        self.player.play("/x/1.opus", 1.0)
        self.assertEqual(self.sent, [])
        self.player.live = [Live(0)] * D.MAX_CONCURRENT     # all exited
        self.player.play("/x/1.opus", 1.0)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.player.live, [])


class KeyFilterTest(unittest.TestCase):
    def test_debounce_same_key_only(self):
        k = D.KeyFilter(debounce_ms=30)
        self.assertTrue(k.on_press(30, 0))
        self.assertFalse(k.on_press(30, 10))
        self.assertTrue(k.on_press(31, 11))      # other key unaffected
        self.assertTrue(k.on_press(30, 31))

    def test_modifiers_sound_on_their_own(self):
        k = D.KeyFilter()
        for code in (42, 54, 58, 100, 125):     # shifts, caps lock, altgr, super
            self.assertTrue(k.on_press(code, 0), code)


class PackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.roots = [os.path.join(self.tmp.name, "shipped"), os.path.join(self.tmp.name, "user")]
        self.d = os.path.join(self.roots[0], "p")
        os.makedirs(os.path.join(self.d, "up"))
        for f in ("default.opus", "30.opus", "row1.opus", "up/default.opus", "up/30.opus"):
            open(os.path.join(self.d, f), "wb").write(b"x")
        with open(os.path.join(self.d, "pack.json"), "w") as f:
            json.dump({"name": "Pack P", "credit": "someone", "source": "https://x", "release": "recorded",
                       "keys": {"31": "row1.opus", "30": "row1.opus", "99": "missing.opus"}}, f)
        os.makedirs(os.path.join(self.roots[1], "mine"))
        open(os.path.join(self.roots[1], "mine", "default.wav"), "wb").write(b"x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_lookup_order_and_fallbacks(self):
        p = D.Pack("p", self.roots)
        self.assertTrue(p.sample_for(30).endswith("/30.opus"))       # explicit file wins over keys map
        self.assertTrue(p.sample_for(31).endswith("/row1.opus"))     # keys map
        self.assertTrue(p.sample_for(99).endswith("/default.opus"))  # mapped file missing
        self.assertTrue(p.sample_for(272).endswith("/default.opus")) # mouse
        self.assertTrue(p.sample_for(30, down=False).endswith("/up/30.opus"))
        self.assertTrue(p.sample_for(31, down=False).endswith("/up/default.opus"))
        self.assertEqual(D.list_packs(self.roots), ["mine", "p"])

    def test_release_optional(self):
        p = D.Pack("mine", self.roots)
        self.assertIsNone(p.release)
        self.assertIsNone(p.sample_for(30, down=False))

    def test_meta(self):
        m = D.pack_meta("p", self.roots)
        self.assertEqual(m, {"id": "p", "name": "Pack P", "credit": "someone", "source": "https://x",
                             "release": "recorded", "user": True})
        self.assertEqual(D.pack_meta("mine", self.roots)["name"], "mine")

    def test_missing_default_raises(self):
        os.makedirs(os.path.join(self.roots[0], "empty"))
        with self.assertRaises(FileNotFoundError):
            D.Pack("empty", self.roots)


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.player = FakePlayer()
        self.ctl = D.Controller(self.player, [SOUNDS], [os.path.join(SOUNDS, "mouse")])
        self.ctl.velocity = False

    def test_loads_first_pack_by_default(self):
        self.assertEqual(self.ctl.pack.name, "mx-blue")
        self.assertEqual(self.ctl.mouse_pack.name, "logitech")

    def test_volumes_clamped_and_separate(self):
        self.assertEqual(self.ctl.handle({"cmd": "volume", "value": 250})["volume"], 100)
        self.assertEqual(self.ctl.handle({"cmd": "mousevolume", "value": 30})["mouse_volume"], 30)
        self.assertEqual(self.ctl.handle({"cmd": "volume", "value": -5})["volume"], 0)
        st = self.ctl.handle({"cmd": "status"})
        self.assertEqual((st["volume"], st["mouse_volume"]), (0, 30))
        self.ctl.handle({"cmd": "volume", "value": 80})
        self.ctl.handle({"cmd": "play", "key": 30})
        self.ctl.handle({"cmd": "play", "key": 272})
        self.assertEqual([round(v, 2) for v in self.player.volumes], [0.8, 0.3])

    def test_mute_load_and_status(self):
        self.assertTrue(self.ctl.handle({"cmd": "mute", "toggle": True})["muted"])
        self.assertEqual(self.ctl.handle({"cmd": "load", "pack": "topre"})["pack"], "topre")
        st = self.ctl.handle({"cmd": "status"})
        self.assertEqual(st["pack"], "topre")
        ids = [p["id"] for p in st["packs"]]
        self.assertIn("mx-blue", ids)
        self.assertEqual(st["packs"][ids.index("topre")]["credit"], "Mechvibes")
        self.assertNotIn("mouse", ids)
        self.assertEqual([p["id"] for p in st["mouse_packs"]],
                         ["chat", "crisp", "deep", "logitech", "ping", "razer", "soft", "studio", "vibrate", "wooden"])
        self.assertEqual(self.ctl.handle({"cmd": "mousepack", "pack": "razer"})["mouse_pack"], "razer")
        self.assertFalse(self.ctl.handle({"cmd": "mousepack", "pack": "nope"})["ok"])
        bad = self.ctl.handle({"cmd": "load", "pack": "nope"})
        self.assertFalse(bad["ok"])

    def test_play_reports_latency_and_respects_mouse_switch(self):
        r = self.ctl.handle({"cmd": "play", "key": 30})
        self.assertTrue(r["ok"])
        self.assertGreaterEqual(r["latency_ms"], 0)
        self.assertEqual(len(self.player.played), 1)
        self.assertTrue(self.player.played[0].endswith("/mx-blue/30.opus"))
        self.assertTrue(self.ctl.handle({"cmd": "play", "key": 272})["ok"])
        self.assertTrue(self.player.played[1].endswith("/mouse/logitech/left.opus"))
        self.assertTrue(self.ctl.handle({"cmd": "play", "key": 273})["ok"])
        self.assertTrue(self.player.played[2].endswith("/mouse/logitech/right.opus"))
        self.assertTrue(self.ctl.handle({"cmd": "play", "key": 30, "down": False})["ok"])
        self.assertTrue(self.player.played[3].endswith("/mx-blue/up/30.opus"))
        self.assertAlmostEqual(self.player.volumes[3], 0.7 * D.RELEASE_GAIN)
        self.ctl.handle({"cmd": "release", "value": False})
        self.assertFalse(self.ctl.handle({"cmd": "play", "key": 30, "down": False})["ok"])
        self.ctl.handle({"cmd": "mouse", "value": False})
        self.assertFalse(self.ctl.handle({"cmd": "play", "key": 272})["ok"])
        self.ctl.handle({"cmd": "enable", "value": False})
        self.assertFalse(self.ctl.handle({"cmd": "play", "key": 30})["ok"])

    def test_velocity_gain(self):
        self.ctl.velocity = True
        self.ctl.handle({"cmd": "volume", "value": 100})
        self.ctl.play(30, 10.0)            # first key: no history, full volume
        self.ctl.play(31, 10.05)           # 50 ms later: full
        self.ctl.play(32, 11.0)            # 950 ms later: relaxed
        self.ctl.play(33, 11.3)            # 300 ms: in between
        v = [round(x, 2) for x in self.player.volumes]
        self.assertEqual(v[:3], [1.0, 1.0, 0.8])
        self.assertTrue(0.8 < v[3] < 1.0)
        self.assertEqual(self.ctl.handle({"cmd": "stats"})["total"], 4)
        self.assertEqual(self.ctl.handle({"cmd": "stats"})["top"][0][1], 1)

    def test_room_and_theme_commands(self):
        started = []
        self.ctl.room.start = lambda name: started.append(name)
        self.assertEqual(self.ctl.handle({"cmd": "room", "value": "wood"})["room"], "wood")
        self.assertEqual(started, ["wood"])
        self.assertEqual(self.ctl.handle({"cmd": "room", "value": "bogus"})["room"], "none")
        self.assertEqual([r["id"] for r in self.ctl.status()["rooms"]], ["desk", "tray", "wood", "wall"])
        r = self.ctl.handle({"cmd": "theme", "slug": "tokyo-night"})
        self.assertEqual((r["evt"], r["slug"]), ("theme", "tokyo-night"))
        self.assertTrue(self.player.played[-1].endswith("/28.opus"))

    def test_unknown_cmd(self):
        self.assertFalse(self.ctl.handle({"cmd": "zzz"})["ok"])


class SoundPacksTest(unittest.TestCase):
    PACKS = ["alpaca", "alps-blue", "box-navy", "buckling-spring", "eg-crystal-purple", "eg-oreo",
             "holy-panda", "ink-black", "ink-red", "mx-black", "mx-black-kbsim", "mx-black-pbt",
             "mx-blue", "mx-blue-kbsim", "mx-blue-pbt", "mx-brown", "mx-brown-kbsim", "mx-brown-pbt",
             "mx-red", "mx-red-pbt", "nk-cream", "nk-cream-kbsim", "topre", "topre-kbsim", "turquoise"]
    MOUSE = ["chat", "crisp", "deep", "logitech", "ping", "razer", "soft", "studio", "vibrate", "wooden"]

    def test_every_pack_is_credited_opus_with_release(self):
        self.assertEqual(D.list_packs([SOUNDS]), self.PACKS)
        self.assertEqual(D.list_packs([os.path.join(SOUNDS, "mouse")]), self.MOUSE)
        for p in self.PACKS + ["mouse/" + m for m in self.MOUSE]:
            d = os.path.join(SOUNDS, p)
            meta = json.load(open(os.path.join(d, "pack.json")))
            for k in ("name", "credit", "source", "license", "release"):
                self.assertTrue(meta.get(k), "%s missing %s" % (p, k))
            self.assertTrue(os.path.isfile(os.path.join(d, "default.opus")), p)
            self.assertTrue(os.path.isfile(os.path.join(d, "up", "default.opus")), p)
            for root, _, files in os.walk(d):
                for f in files:
                    if f == "pack.json":
                        continue
                    self.assertTrue(f.endswith(".opus"), f)
                    self.assertLess(os.path.getsize(os.path.join(root, f)), 12000, "%s/%s too big" % (p, f))
            roots = [os.path.join(SOUNDS, "mouse")] if p.startswith("mouse/") else [SOUNDS]
            pack = D.Pack(p.split("/")[-1], roots)
            for code in (1, 30, 57, 28, 14, 105, 272, 273):
                self.assertTrue(os.path.isfile(pack.sample_for(code)))
                self.assertTrue(os.path.isfile(pack.sample_for(code, down=False)))

    def test_every_sample_opens_in_libsndfile(self):
        """pw-play decodes via libsndfile; a rejected file is a silent key."""
        import ctypes
        sf = ctypes.CDLL("libsndfile.so.1")
        sf.sf_open.restype = ctypes.c_void_p
        sf.sf_close.argtypes = [ctypes.c_void_p]
        info = (ctypes.c_int64 * 8)()
        bad = []
        for root, _, files in os.walk(SOUNDS):
            for f in files:
                if not f.endswith(".opus"):
                    continue
                h = sf.sf_open(os.path.join(root, f).encode(), 0x10, ctypes.byref(info))
                if h:
                    sf.sf_close(h)
                else:
                    bad.append(os.path.join(root, f))
        self.assertEqual(bad, [])


class SocketRoundTripTest(unittest.TestCase):
    def test_daemon_answers_over_socket(self):
        tmp = tempfile.mkdtemp()
        sock = os.path.join(tmp, "ctl.sock")
        proc = subprocess.Popen(DAEMON_CMD + ["--socket=" + sock],
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            for _ in range(50):
                if os.path.exists(sock):
                    break
                time.sleep(0.05)
            c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            c.connect(sock)
            c.settimeout(2)
            f = c.makefile("rwb", buffering=0)
            hello = json.loads(f.readline())
            self.assertEqual(hello["evt"], "hello")
            self.assertEqual(hello["pack"], "mx-blue")

            def rpc(obj):
                f.write((json.dumps(obj) + "\n").encode())
                return json.loads(f.readline())

            self.assertEqual(rpc({"cmd": "ping", "t": 5, "id": 1}), {"ok": True, "pong": 5, "id": 1})
            self.assertEqual(rpc({"cmd": "volume", "value": 40})["volume"], 40)
            self.assertEqual(rpc({"cmd": "load", "pack": "mx-brown"})["pack"], "mx-brown")
            self.assertEqual(rpc({"cmd": "status"})["volume"], 40)
            self.assertTrue(rpc({"cmd": "play", "key": 57})["ok"])
            self.assertTrue(rpc({"cmd": "quit"})["quit"])
            self.assertEqual(proc.wait(timeout=3), 0)
            self.assertFalse(os.path.exists(sock))
        finally:
            if proc.poll() is None:
                proc.kill()
            err = proc.stderr.read().decode()
            self.assertEqual(err, "", err)

    def test_second_daemon_refused_by_lock(self):
        tmp = tempfile.mkdtemp()
        sock = os.path.join(tmp, "ctl.sock")
        a = subprocess.Popen(DAEMON_CMD + ["--socket=" + sock], stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                if os.path.exists(sock):
                    break
                time.sleep(0.05)
            b = subprocess.run(DAEMON_CMD + ["--socket=" + sock], timeout=5)
            self.assertEqual(b.returncode, 2)
        finally:
            a.kill()

    def test_pipe_channel_speaks_same_protocol(self):
        tmp = tempfile.mkdtemp()
        sock = os.path.join(tmp, "ctl.sock")
        p = subprocess.Popen(DAEMON_CMD + ["--socket=" + sock], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            hello = json.loads(p.stdout.readline())
            self.assertEqual(hello["evt"], "hello")
            self.assertIn("denied", hello)

            def rpc(obj):
                p.stdin.write((json.dumps(obj) + "\n").encode())
                p.stdin.flush()
                return json.loads(p.stdout.readline())

            self.assertEqual(rpc({"cmd": "ping", "t": 7, "id": 3}), {"ok": True, "pong": 7, "id": 3})
            self.assertTrue(rpc({"cmd": "mute", "toggle": True})["muted"])
            self.assertTrue(rpc({"cmd": "status"})["muted"])
            self.assertTrue(rpc({"cmd": "quit"})["quit"])
            self.assertEqual(p.wait(timeout=3), 0)
            self.assertEqual(p.stderr.read().decode(), "")
        finally:
            if p.poll() is None:
                p.kill()

    def test_daemon_exits_when_parent_stdin_closes(self):
        tmp = tempfile.mkdtemp()
        sock = os.path.join(tmp, "ctl.sock")
        p = subprocess.Popen(DAEMON_CMD + ["--socket=" + sock], stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            if os.path.exists(sock):
                break
            time.sleep(0.05)
        p.stdin.close()
        self.assertEqual(p.wait(timeout=3), 0)




class InputReaderTest(unittest.TestCase):
    def test_parses_key_down_events_only(self):
        r = D.InputReader()
        rd, wr = os.pipe()
        r.fds[rd] = "fake"
        ev = D.INPUT_EVENT
        os.write(wr, b"".join([
            ev.pack(0, 0, D.EV_KEY, 30, 1),     # a down
            ev.pack(0, 0, D.EV_KEY, 30, 0),     # a up (ignored)
            ev.pack(0, 0, D.EV_KEY, 30, 2),     # autorepeat (ignored)
            ev.pack(0, 0, 0x02, 0, 5),          # EV_REL mouse move (ignored)
            ev.pack(0, 0, D.EV_KEY, 0x110, 1),  # BTN_LEFT down
            ev.pack(0, 0, D.EV_KEY, 0x160, 1),  # BTN_JOYSTICK-ish (ignored)
            ev.pack(0, 0, 0x00, 0, 0),          # SYN_REPORT
        ]))
        self.assertEqual(list(r.read_presses(rd)), [30, 0x110])
        os.write(wr, ev.pack(0, 0, D.EV_KEY, 30, 1) + ev.pack(0, 0, D.EV_KEY, 30, 0))
        self.assertEqual(list(r.read_events(rd)), [(30, True), (30, False)])
        os.close(wr)
        self.assertEqual(list(r.read_presses(rd)), [])   # EOF closes fd
        self.assertNotIn(rd, r.fds)


if __name__ == "__main__":
    unittest.main()
