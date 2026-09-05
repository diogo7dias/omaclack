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
DAEMON = os.path.join(ROOT, "bin", "soundtapd")
SOUNDS = os.path.join(ROOT, "sounds")


def load_daemon():
    loader = importlib.machinery.SourceFileLoader("soundtapd", DAEMON)
    spec = importlib.util.spec_from_loader("soundtapd", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


D = load_daemon()


class FakePlayer:
    def __init__(self):
        self.volume = 0.7
        self.muted = False
        self.stream_ok = True
        self.played = []

    def play(self, path):
        self.played.append(path)


class OneShotPlayerTest(unittest.TestCase):
    """Exercise OneShotPlayer without spawning pw-play by stubbing _spawn."""

    def setUp(self):
        self.player = D.OneShotPlayer()
        self.sent = []
        self.player._spawn = lambda cmd: self.sent.append(cmd)

    def test_passes_gain_and_path_to_pw_play(self):
        self.player.volume = 0.5
        self.player.play("/x/30.opus")
        self.assertEqual(self.sent, [["pw-play", "--volume", "0.500", "/x/30.opus"]])

    def test_muted_or_silent_or_empty_plays_nothing(self):
        self.player.muted = True
        self.player.play("/x/30.opus")
        self.player.muted = False
        self.player.volume = 0.0
        self.player.play("/x/30.opus")
        self.player.volume = 1.0
        self.player.play(None)
        self.assertEqual(self.sent, [])

    def test_concurrency_cap_counts_only_running_children(self):
        class Live:
            def __init__(self, rc): self.rc = rc
            def poll(self): return self.rc
        self.player.live = [Live(None)] * D.MAX_CONCURRENT
        self.player.play("/x/1.opus")
        self.assertEqual(self.sent, [])
        self.player.live = [Live(0)] * D.MAX_CONCURRENT     # all exited
        self.player.play("/x/1.opus")
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
        self.d = os.path.join(self.tmp.name, "p")
        os.makedirs(self.d)
        for f in ("default.opus", "30.opus", "row1.opus"):
            open(os.path.join(self.d, f), "wb").write(b"x")
        with open(os.path.join(self.d, "pack.json"), "w") as f:
            json.dump({"name": "Pack P", "credit": "someone", "source": "https://x",
                       "keys": {"31": "row1.opus", "30": "row1.opus", "99": "missing.opus"}}, f)

    def tearDown(self):
        self.tmp.cleanup()

    def test_lookup_order_and_fallbacks(self):
        p = D.Pack("p", self.tmp.name)
        self.assertTrue(p.sample_for(30).endswith("/30.opus"))       # explicit file wins over keys map
        self.assertTrue(p.sample_for(31).endswith("/row1.opus"))     # keys map
        self.assertTrue(p.sample_for(99).endswith("/default.opus"))  # mapped file missing
        self.assertTrue(p.sample_for(272).endswith("/default.opus")) # mouse
        self.assertEqual(D.list_packs(self.tmp.name), ["p"])

    def test_meta(self):
        m = D.pack_meta("p", self.tmp.name)
        self.assertEqual(m, {"id": "p", "name": "Pack P", "credit": "someone", "source": "https://x"})
        os.makedirs(os.path.join(self.tmp.name, "bare"))
        open(os.path.join(self.tmp.name, "bare", "default.wav"), "wb").write(b"x")
        self.assertEqual(D.pack_meta("bare", self.tmp.name)["name"], "bare")

    def test_missing_default_raises(self):
        os.makedirs(os.path.join(self.tmp.name, "empty"))
        with self.assertRaises(FileNotFoundError):
            D.Pack("empty", self.tmp.name)


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.player = FakePlayer()
        self.ctl = D.Controller(self.player, SOUNDS)

    def test_loads_first_pack_by_default(self):
        self.assertEqual(self.ctl.pack.name, "mx-blue")
        self.assertEqual(self.ctl.mouse_pack.name, "logitech")

    def test_volume_clamped(self):
        self.assertEqual(self.ctl.handle({"cmd": "volume", "value": 250})["volume"], 100)
        self.assertEqual(self.player.volume, 1.0)
        self.assertEqual(self.ctl.handle({"cmd": "volume", "value": -5})["volume"], 0)

    def test_mute_load_and_status(self):
        self.assertTrue(self.ctl.handle({"cmd": "mute", "toggle": True})["muted"])
        self.assertEqual(self.ctl.handle({"cmd": "load", "pack": "topre"})["pack"], "topre")
        st = self.ctl.handle({"cmd": "status"})
        self.assertEqual(st["pack"], "topre")
        ids = [p["id"] for p in st["packs"]]
        self.assertIn("mx-blue", ids)
        self.assertEqual(st["packs"][ids.index("topre")]["credit"], "Mechvibes")
        self.assertNotIn("mouse", ids)
        self.assertEqual([p["id"] for p in st["mouse_packs"]], ["crisp", "logitech", "razer"])
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
        self.ctl.handle({"cmd": "mouse", "value": False})
        self.assertFalse(self.ctl.handle({"cmd": "play", "key": 272})["ok"])
        self.ctl.handle({"cmd": "enable", "value": False})
        self.assertFalse(self.ctl.handle({"cmd": "play", "key": 30})["ok"])

    def test_unknown_cmd(self):
        self.assertFalse(self.ctl.handle({"cmd": "zzz"})["ok"])


class SoundPacksTest(unittest.TestCase):
    PACKS = ["alps-blue", "box-navy", "buckling-spring", "holy-panda",
             "mx-black", "mx-blue", "mx-brown", "mx-red", "topre"]

    MOUSE = ["crisp", "logitech", "razer"]

    def test_every_pack_is_credited_opus_and_small(self):
        self.assertEqual(D.list_packs(SOUNDS), self.PACKS)
        self.assertEqual(D.list_packs(os.path.join(SOUNDS, "mouse")), self.MOUSE)
        for p in self.PACKS + ["mouse/" + m for m in self.MOUSE]:
            d = os.path.join(SOUNDS, p)
            meta = json.load(open(os.path.join(d, "pack.json")))
            for k in ("name", "credit", "source", "license"):
                self.assertTrue(meta.get(k), "%s missing %s" % (p, k))
            files = [f for f in os.listdir(d) if f != "pack.json"]
            self.assertIn("default.opus", files)
            for f in files:
                self.assertTrue(f.endswith(".opus"), f)
                with open(os.path.join(d, f), "rb") as fh:
                    head = fh.read(4)
                self.assertEqual(head, b"OggS", "%s/%s is not an Ogg container" % (p, f))
                self.assertLess(os.path.getsize(os.path.join(d, f)), 12000, "%s/%s too big" % (p, f))
            pack = D.Pack(p, SOUNDS)
            for code in (1, 30, 57, 28, 14, 105, 272, 273):
                self.assertTrue(os.path.isfile(pack.sample_for(code)))

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
        proc = subprocess.Popen([sys.executable, DAEMON, "--socket=" + sock],
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
        a = subprocess.Popen([sys.executable, DAEMON, "--socket=" + sock], stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                if os.path.exists(sock):
                    break
                time.sleep(0.05)
            b = subprocess.run([sys.executable, DAEMON, "--socket=" + sock], timeout=5)
            self.assertEqual(b.returncode, 2)
        finally:
            a.kill()

    def test_pipe_channel_speaks_same_protocol(self):
        tmp = tempfile.mkdtemp()
        sock = os.path.join(tmp, "ctl.sock")
        p = subprocess.Popen([sys.executable, DAEMON, "--socket=" + sock], stdin=subprocess.PIPE,
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
        p = subprocess.Popen([sys.executable, DAEMON, "--socket=" + sock], stdin=subprocess.PIPE,
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
        os.close(wr)
        self.assertEqual(list(r.read_presses(rd)), [])   # EOF closes fd
        self.assertNotIn(rd, r.fds)


if __name__ == "__main__":
    unittest.main()
