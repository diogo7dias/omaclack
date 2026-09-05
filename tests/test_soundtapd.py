import array
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
import wave

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


class FakeMixer:
    def __init__(self):
        self.volume = 0.7
        self.muted = False
        self.stream_ok = True
        self.triggered = []

    def trigger(self, samples):
        self.triggered.append(samples)


def write_wav(path, samples, channels=1):
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(array.array("h", samples).tobytes())


class MixChunkTest(unittest.TestCase):
    def test_sums_voices_and_drops_finished(self):
        a = array.array("h", [100, 100, 100])
        b = array.array("h", [1, 1, 1, 1, 1])
        voices = [[a, 0], [b, 0]]
        out = D.mix_chunk(voices, 4, 1.0)
        self.assertEqual(list(out), [101, 101, 101, 1])
        self.assertEqual(len(voices), 1)  # a finished, b still has 1 frame
        self.assertEqual(voices[0][1], 4)

    def test_clips_and_applies_gain(self):
        a = array.array("h", [30000])
        b = array.array("h", [30000])
        out = D.mix_chunk([[a, 0], [b, 0]], 1, 1.0)
        self.assertEqual(out[0], 32767)
        out = D.mix_chunk([[array.array("h", [-30000]), 0], [array.array("h", [-30000]), 0]], 1, 1.0)
        self.assertEqual(out[0], -32768)
        out = D.mix_chunk([[array.array("h", [1000]), 0]], 1, 0.5)
        self.assertEqual(out[0], 500)


class KeyFilterTest(unittest.TestCase):
    def test_debounce_same_key(self):
        k = D.KeyFilter(debounce_ms=30, grace_ms=100)
        self.assertEqual(k.on_press(30, 0), [30])
        self.assertEqual(k.on_press(30, 10), [])
        self.assertEqual(k.on_press(30, 31), [30])

    def test_modifier_alone_is_silent(self):
        k = D.KeyFilter()
        self.assertEqual(k.on_press(42, 0), [])
        k.flush(200)
        self.assertIsNone(k.next_deadline())
        self.assertEqual(k.on_press(30, 300), [30])

    def test_modifier_then_key_within_grace_sounds_both(self):
        k = D.KeyFilter()
        k.on_press(42, 0)
        self.assertEqual(k.next_deadline(), 100)
        self.assertEqual(k.on_press(30, 50), [42, 30])

    def test_modifier_then_key_after_grace_sounds_key_only(self):
        k = D.KeyFilter()
        k.on_press(42, 0)
        self.assertEqual(k.on_press(30, 150), [30])


class PackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = os.path.join(self.tmp.name, "p")
        os.makedirs(d)
        write_wav(os.path.join(d, "default.wav"), [1, 2, 3])
        write_wav(os.path.join(d, "30.wav"), [9, 9])
        write_wav(os.path.join(d, "mouse.wav"), [5])
        write_wav(os.path.join(d, "stereo.wav"), [10, 20, 30, 40], channels=2)

    def tearDown(self):
        self.tmp.cleanup()

    def test_lookup_and_fallbacks(self):
        p = D.Pack("p", self.tmp.name)
        self.assertEqual(list(p.sample_for(30)), [9, 9])
        self.assertEqual(list(p.sample_for(31)), [1, 2, 3])
        self.assertEqual(list(p.sample_for(272)), [5])
        self.assertEqual(D.list_packs(self.tmp.name), ["p"])

    def test_missing_default_raises(self):
        os.makedirs(os.path.join(self.tmp.name, "empty"))
        with self.assertRaises(FileNotFoundError):
            D.Pack("empty", self.tmp.name)

    def test_stereo_downmix(self):
        s = D.load_wav(os.path.join(self.tmp.name, "p", "stereo.wav"))
        self.assertEqual(list(s), [15, 35])


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.mixer = FakeMixer()
        self.ctl = D.Controller(self.mixer, SOUNDS)

    def test_loads_first_pack_by_default(self):
        self.assertEqual(self.ctl.pack.name, "cherry-blue")

    def test_volume_clamped(self):
        self.assertEqual(self.ctl.handle({"cmd": "volume", "value": 250})["volume"], 100)
        self.assertEqual(self.mixer.volume, 1.0)
        self.assertEqual(self.ctl.handle({"cmd": "volume", "value": -5})["volume"], 0)

    def test_mute_load_and_status(self):
        self.assertTrue(self.ctl.handle({"cmd": "mute", "toggle": True})["muted"])
        self.assertEqual(self.ctl.handle({"cmd": "load", "pack": "topre"})["pack"], "topre")
        st = self.ctl.handle({"cmd": "status"})
        self.assertEqual(st["pack"], "topre")
        self.assertIn("typewrite", st["packs"])
        bad = self.ctl.handle({"cmd": "load", "pack": "nope"})
        self.assertFalse(bad["ok"])

    def test_play_reports_latency_and_respects_mouse_switch(self):
        r = self.ctl.handle({"cmd": "play", "key": 30})
        self.assertTrue(r["ok"])
        self.assertGreaterEqual(r["latency_ms"], 0)
        self.assertEqual(len(self.mixer.triggered), 1)
        self.ctl.handle({"cmd": "mouse", "value": False})
        self.assertFalse(self.ctl.handle({"cmd": "play", "key": 272})["ok"])
        self.ctl.handle({"cmd": "enable", "value": False})
        self.assertFalse(self.ctl.handle({"cmd": "play", "key": 30})["ok"])

    def test_unknown_cmd(self):
        self.assertFalse(self.ctl.handle({"cmd": "zzz"})["ok"])


class SoundPacksTest(unittest.TestCase):
    def test_every_pack_file_is_short_mono_44k(self):
        packs = D.list_packs(SOUNDS)
        self.assertEqual(packs, ["cherry-blue", "cherry-brown", "topre", "typewrite"])
        for p in packs:
            d = os.path.join(SOUNDS, p)
            files = [f for f in os.listdir(d) if f.endswith(".wav")]
            self.assertGreaterEqual(len(files), 10, p)
            self.assertIn("default.wav", files)
            for f in files:
                with wave.open(os.path.join(d, f)) as w:
                    self.assertEqual(w.getnchannels(), 1, f)
                    self.assertEqual(w.getframerate(), 44100, f)
                    self.assertEqual(w.getsampwidth(), 2, f)
                    self.assertLess(w.getnframes() / 44100.0, 0.080, "%s/%s too long" % (p, f))
                    self.assertGreater(w.getnframes(), 400, f)


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
            self.assertEqual(hello["pack"], "cherry-blue")

            def rpc(obj):
                f.write((json.dumps(obj) + "\n").encode())
                return json.loads(f.readline())

            self.assertEqual(rpc({"cmd": "ping", "t": 5, "id": 1}), {"ok": True, "pong": 5, "id": 1})
            self.assertEqual(rpc({"cmd": "volume", "value": 40})["volume"], 40)
            self.assertEqual(rpc({"cmd": "load", "pack": "typewrite"})["pack"], "typewrite")
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
