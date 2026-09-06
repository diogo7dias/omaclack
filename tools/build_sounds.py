#!/usr/bin/env python3
"""Import sound packs into sounds/.

Usage: tools/build_sounds.py <mechvibes> <mechvibes-dx> <kbsim> <typetone>
       (paths to checkouts of the four upstream repos)

Layout per pack:
  sounds/<pack>/<code>.opus        key press,  stereo 48 kHz Opus, panned by key position
  sounds/<pack>/up/<code>.opus     key release
  sounds/<pack>/{,up/}default.opus fallback
  sounds/<pack>/pack.json          name, credit, source, license, optional "keys" map
  sounds/mouse/<pack>/...          same shape with left/right/middle instead of codes

Every file is opened through libsndfile after encoding (that is what pw-play
decodes with) and re-encoded with a longer tail if it is rejected.
"""
import ctypes
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "sounds")

MECHVIBES = "https://github.com/hainguyents13/mechvibes"
MECHVIBES_DX = "https://github.com/hainguyents13/mechvibes-dx"
KBSIM = "https://github.com/tplai/kbsim"
TYPETONE = "https://github.com/phuclh/omarchy-typetone"

# ------------------------------------------------------------------ pack lists

# MechvibesDX "single" packs: one sprite, per-key press AND release timings.
DX_PACKS = [
    ("cherrymx-blue-abs", "mx-blue", "Cherry MX Blue"),
    ("cherrymx-blue-pbt", "mx-blue-pbt", "Cherry MX Blue, PBT caps"),
    ("cherrymx-brown-abs", "mx-brown", "Cherry MX Brown"),
    ("cherrymx-brown-pbt", "mx-brown-pbt", "Cherry MX Brown, PBT caps"),
    ("cherrymx-red-abs", "mx-red", "Cherry MX Red"),
    ("cherrymx-black-abs", "mx-black", "Cherry MX Black"),
    ("cherrymx-black-pbt", "mx-black-pbt", "Cherry MX Black, PBT caps"),
    ("topre-purple-hybrid-pbt", "topre", "Topre"),
    ("eg-oreo", "eg-oreo", "Everglide Oreo"),
    ("eg-crystal-purple", "eg-crystal-purple", "Everglide Crystal Purple"),
]
# Old Mechvibes "single" packs with press only; release is derived from the press.
MV_PACKS = [
    ("cherrymx-red-pbt", "mx-red-pbt", "Cherry MX Red, PBT caps"),
]
# Old Mechvibes "multi" pack: one WAV per key, press only.
MV_MULTI_PACKS = [
    ("nk-cream", "nk-cream", "Novelkeys Cream"),
]
# kbsim: five row samples + space/enter/backspace, press and release.
KB_PACKS = [
    ("holypanda", "holy-panda", "Holy Panda"),
    ("buckling", "buckling-spring", "Buckling Spring"),
    ("boxnavy", "box-navy", "Kailh Box Navy"),
    ("bluealps", "alps-blue", "Alps Blue"),
    ("alpaca", "alpaca", "Alpaca"),
    ("blackink", "ink-black", "Gateron Ink Black"),
    ("redink", "ink-red", "Gateron Ink Red"),
    ("cream", "nk-cream-kbsim", "Novelkeys Cream, kbsim"),
    ("mxblack", "mx-black-kbsim", "Cherry MX Black, kbsim"),
    ("mxblue", "mx-blue-kbsim", "Cherry MX Blue, kbsim"),
    ("mxbrown", "mx-brown-kbsim", "Cherry MX Brown, kbsim"),
    ("topre", "topre-kbsim", "Topre, kbsim"),
    ("turquoise", "turquoise", "Tecsee Turquoise"),
]
# Typetone mouse renders: press at ~0-20 ms, release ~100 ms later, then a
# second click we drop. `split` is where press ends and release begins.
TT_MOUSE = [
    ("logitech", "logitech", "Logitech", "OwlStorm, Freesound 320146 (CC0) via Typetone", 0.075, 0.150),
    ("razer", "razer", "Razer", "Katsuhira, Freesound 555394 (CC0) via Typetone", 0.075, 0.160),
    ("crisp", "crisp", "Crisp", "Six Ways, Freesound 223445 (CC0) via Typetone", 0.060, 0.135),
    ("soft", "soft", "Soft", "Breviceps, Freesound 447938 (CC0) via Typetone", 0.075, 0.175),
    ("deep", "deep", "Deep", "Breviceps, Freesound 447938 (CC0) via Typetone", 0.090, 0.200),
    ("studio", "studio", "Studio", "1j01, OpenGameArt middle click (CC0) via Typetone", 0.040, 0.075),
]
# MechvibesDX mouse packs: sprite with press/release timings.
DX_MOUSE = [
    ("wooden", "wooden", "Wooden"),
    ("ping", "ping", "Ping"),
    ("chat", "chat", "Chat"),
    ("vibrate", "vibrate", "Vibrate"),
]

# ------------------------------------------------------------------ key maps

# Browser KeyboardEvent.code names (MechvibesDX) -> Linux KEY_* codes.
DX_CODES = {
    "Escape": 1, "Digit1": 2, "Digit2": 3, "Digit3": 4, "Digit4": 5, "Digit5": 6, "Digit6": 7,
    "Digit7": 8, "Digit8": 9, "Digit9": 10, "Digit0": 11, "Minus": 12, "Equal": 13, "Backspace": 14,
    "Tab": 15, "KeyQ": 16, "KeyW": 17, "KeyE": 18, "KeyR": 19, "KeyT": 20, "KeyY": 21, "KeyU": 22,
    "KeyI": 23, "KeyO": 24, "KeyP": 25, "BracketLeft": 26, "BracketRight": 27, "Enter": 28,
    "ControlLeft": 29, "KeyA": 30, "KeyS": 31, "KeyD": 32, "KeyF": 33, "KeyG": 34, "KeyH": 35,
    "KeyJ": 36, "KeyK": 37, "KeyL": 38, "Semicolon": 39, "Quote": 40, "Backquote": 41,
    "ShiftLeft": 42, "Backslash": 43, "KeyZ": 44, "KeyX": 45, "KeyC": 46, "KeyV": 47, "KeyB": 48,
    "KeyN": 49, "KeyM": 50, "Comma": 51, "Period": 52, "Slash": 53, "ShiftRight": 54,
    "NumpadMultiply": 55, "AltLeft": 56, "Space": 57, "CapsLock": 58, "F1": 59, "F2": 60, "F3": 61,
    "F4": 62, "F5": 63, "F6": 64, "F7": 65, "F8": 66, "F9": 67, "F10": 68, "NumLock": 69,
    "ScrollLock": 70, "Numpad7": 71, "Numpad8": 72, "Numpad9": 73, "NumpadSubtract": 74,
    "Numpad4": 75, "Numpad5": 76, "Numpad6": 77, "NumpadAdd": 78, "Numpad1": 79, "Numpad2": 80,
    "Numpad3": 81, "Numpad0": 82, "NumpadDecimal": 83, "F11": 87, "F12": 88, "NumpadEnter": 96,
    "ControlRight": 97, "NumpadDivide": 98, "PrintScreen": 99, "AltRight": 100, "Home": 102,
    "ArrowUp": 103, "PageUp": 104, "ArrowLeft": 105, "ArrowRight": 106, "End": 107,
    "ArrowDown": 108, "PageDown": 109, "Insert": 110, "Delete": 111, "Pause": 119,
    "MetaLeft": 125, "MetaRight": 126, "ContextMenu": 127,
}
# iohook raw codes (old Mechvibes): < 0x100 equal Linux; extended scan codes
# appear as 0xE00|sc or 0xE000|sc.
EXT = {0x1C: 96, 0x1D: 97, 0x35: 98, 0x37: 99, 0x38: 100, 0x47: 102, 0x48: 103,
       0x49: 104, 0x4B: 105, 0x4D: 106, 0x4F: 107, 0x50: 108, 0x51: 109,
       0x52: 110, 0x53: 111, 0x5B: 125, 0x5C: 126, 0x5D: 127}


def iohook_code(raw):
    if not str(raw).isdigit():
        return None
    raw = int(raw)
    if raw < 0x100:
        return raw
    for base in (0xE00, 0xE000):
        if raw & ~0xFF == base and (raw & 0xFF) in EXT:
            return EXT[raw & 0xFF]
    return None


# Rows of a standard board, for kbsim's five generic samples and for panning.
ROWS = {
    0: [1] + list(range(59, 69)) + [87, 88, 99, 70, 119],           # esc, F1-F12, prtsc, scroll, pause
    1: list(range(2, 15)) + [41],                                     # ` 1..= backspace
    2: [15] + list(range(16, 28)) + [43],                             # tab q..] backslash
    3: [58] + list(range(30, 41)) + [28],                             # caps a..' enter
    4: [42] + list(range(44, 54)) + [54, 29, 125, 56, 57, 100, 126, 127, 97],
}
SPECIAL = {14: "backspace", 28: "enter", 57: "space"}


def key_pan(code):
    """-1 (left) .. +1 (right) for a key on an ANSI board; nav/numpad sit right."""
    order = {
        1: [1] + list(range(59, 69)) + [87, 88],
        2: [41] + list(range(2, 15)),
        3: [15] + list(range(16, 28)) + [43],
        4: [58] + list(range(30, 41)) + [28],
        5: [42] + list(range(44, 54)) + [54],
        6: [29, 125, 56, 57, 100, 126, 127, 97],
    }
    for row in order.values():
        if code in row:
            i = row.index(code)
            return (i / max(1, len(row) - 1)) * 2 - 1
    if 71 <= code <= 83 or code in (55, 69, 74, 78, 96, 98):
        return 0.9                                   # numpad
    if code in (99, 70, 119, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111):
        return 0.7                                   # nav cluster
    return 0.0


# ------------------------------------------------------------------ encoding

_sf = ctypes.CDLL("libsndfile.so.1")
_sf.sf_open.restype = ctypes.c_void_p
_sf.sf_close.argtypes = [ctypes.c_void_p]


class _SfInfo(ctypes.Structure):
    _fields_ = [("frames", ctypes.c_int64), ("samplerate", ctypes.c_int), ("channels", ctypes.c_int),
                ("format", ctypes.c_int), ("sections", ctypes.c_int), ("seekable", ctypes.c_int)]


def sndfile_accepts(path):
    h = _sf.sf_open(path.encode(), 0x10, ctypes.byref(_SfInfo()))
    if not h:
        return False
    _sf.sf_close(h)
    return True


def peak_gain(src, start=None, dur=None):
    """Gain (dB) that brings the peak to -1 dBFS."""
    cmd = ["ffmpeg", "-v", "info"]
    if start is not None:
        cmd += ["-ss", "%.3f" % start, "-t", "%.3f" % dur]
    cmd += ["-i", src, "-af", "volumedetect", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    m = re.search(r"max_volume: (-?[\d.]+) dB", r.stderr)
    return (-1.0 - float(m.group(1))) if m else 0.0


PAN_DEPTH = 0.35   # 0 = mono, 1 = hard pan


def encode(src, dst, start=None, dur=None, gain_db=0.0, pan=None, extra_filters=""):
    """Slice, gain, fade, optional constant-power pan, Opus. Validated by libsndfile."""
    stereo = pan is not None
    if stereo:
        p = max(-1.0, min(1.0, pan)) * PAN_DEPTH
        l = ((1 - p) / 2) ** 0.5 * 1.15
        r = ((1 + p) / 2) ** 0.5 * 1.15
        panf = ",pan=stereo|c0=%.3f*c0|c1=%.3f*c0" % (l, r)
    else:
        panf = ""
    fade_at = max(0.0, (dur or 0.2) - 0.02)
    for pad in (0.02, 0.04, 0.06, 0.08, 0.10, 0.14):
        cmd = ["ffmpeg", "-v", "error", "-y"]
        if start is not None:
            cmd += ["-ss", "%.3f" % start, "-t", "%.3f" % dur]
        af = "aformat=channel_layouts=mono,volume=%.2fdB%s,afade=t=out:st=%.3f:d=0.02,apad=pad_dur=%.2f%s" % (
            gain_db, ("," + extra_filters) if extra_filters else "", fade_at, pad, panf)
        cmd += ["-i", src, "-ar", "48000", "-af", af,
                "-c:a", "libopus", "-b:a", "48k" if stereo else "40k", "-vbr", "on",
                "-frame_duration", "60", "-application", "audio", dst]
        subprocess.run(cmd, check=True)
        if sndfile_accepts(dst):
            return
    raise RuntimeError("libsndfile rejects every encoding of " + dst)


def derive_release(press_src, dst, start, dur, gain_db, pan):
    """No recording of the release: a short, quieter, slightly higher copy of the press."""
    encode(press_src, dst, start, min(dur, 0.07), gain_db - 9.0, pan,
           extra_filters="asetrate=48000*1.12,aresample=48000")


def fresh(path):
    os.makedirs(path, exist_ok=True)
    for f in os.listdir(path):
        fp = os.path.join(path, f)
        if os.path.isfile(fp):
            os.remove(fp)


def link_default(d, pick):
    src = os.path.join(d, pick)
    if os.path.exists(src):
        os.link(src, os.path.join(d, "default.opus"))


def write_pack(rel, meta):
    d = os.path.join(OUT, rel)
    with open(os.path.join(d, "pack.json"), "w") as f:
        json.dump(meta, f, indent=2, sort_keys=True)
        f.write("\n")
    n = sum(len([x for x in files if x.endswith(".opus")]) for _, _, files in os.walk(d))
    print("%-20s %3d files" % (rel, n))


def clear_pack(rel):
    d = os.path.join(OUT, rel)
    fresh(d)
    fresh(os.path.join(d, "up"))


# ------------------------------------------------------------------ builders

def build_dx(root):
    for folder, pid, name in DX_PACKS:
        src_dir = os.path.join(root, "soundpacks", "keyboard", folder)
        cfg = json.load(open(os.path.join(src_dir, "config.json")))
        sprite = os.path.join(src_dir, cfg["audio_file"])
        clear_pack(pid)
        out = os.path.join(OUT, pid)
        gain = peak_gain(sprite)
        for kname, d in cfg["definitions"].items():
            code = DX_CODES.get(kname)
            if code is None:
                continue
            (p0, p1), (r0, r1) = d["timing"]
            pan = key_pan(code)
            encode(sprite, os.path.join(out, "%d.opus" % code), p0 / 1000, min(p1 - p0, 250) / 1000, gain, pan)
            encode(sprite, os.path.join(out, "up", "%d.opus" % code), r0 / 1000, min(r1 - r0, 200) / 1000, gain - 3.0, pan)
        link_default(out, "30.opus")
        link_default(os.path.join(out, "up"), "30.opus")
        write_pack(pid, {"name": name, "credit": "Mechvibes", "source": MECHVIBES_DX,
                         "origin": "soundpacks/keyboard/" + folder, "license": "MIT",
                         "release": "recorded", "stereo": True})


def build_mv(root):
    for folder, pid, name in MV_PACKS:
        src_dir = os.path.join(root, "src", "audio", folder)
        cfg = json.load(open(os.path.join(src_dir, "config.json")))
        sprite = os.path.join(src_dir, cfg["sound"])
        clear_pack(pid)
        out = os.path.join(OUT, pid)
        gain = peak_gain(sprite)
        for raw, span in cfg["defines"].items():
            code = iohook_code(raw)
            if code is None or not isinstance(span, list):
                continue
            st, du = span[0] / 1000, min(span[1], 250) / 1000
            pan = key_pan(code)
            encode(sprite, os.path.join(out, "%d.opus" % code), st, du, gain, pan)
            derive_release(sprite, os.path.join(out, "up", "%d.opus" % code), st, du, gain, pan)
        link_default(out, "30.opus")
        link_default(os.path.join(out, "up"), "30.opus")
        write_pack(pid, {"name": name, "credit": "Mechvibes", "source": MECHVIBES,
                         "origin": "src/audio/" + folder, "license": "MIT",
                         "release": "derived", "stereo": True})


def build_mv_multi(root):
    for folder, pid, name in MV_MULTI_PACKS:
        src_dir = os.path.join(root, "src", "audio", folder)
        cfg = json.load(open(os.path.join(src_dir, "config.json")))
        clear_pack(pid)
        out = os.path.join(OUT, pid)
        files = sorted(set(v for v in cfg["defines"].values() if isinstance(v, str)))
        gain = min(peak_gain(os.path.join(src_dir, f)) for f in files)
        for raw, fn in cfg["defines"].items():
            code = iohook_code(raw)
            if code is None or not isinstance(fn, str):
                continue
            src = os.path.join(src_dir, fn)
            if not os.path.isfile(src):
                continue
            pan = key_pan(code)
            encode(src, os.path.join(out, "%d.opus" % code), 0.0, 0.25, gain, pan)
            derive_release(src, os.path.join(out, "up", "%d.opus" % code), 0.0, 0.25, gain, pan)
        link_default(out, "30.opus")
        link_default(os.path.join(out, "up"), "30.opus")
        write_pack(pid, {"name": name, "credit": "Mechvibes (recorded by Ryan)", "source": MECHVIBES,
                         "origin": "src/audio/" + folder, "license": "MIT",
                         "release": "derived", "stereo": True})


def build_kbsim(root):
    for folder, pid, name in KB_PACKS:
        base = os.path.join(root, "src", "assets", "audio", folder)
        press, rel = os.path.join(base, "press"), os.path.join(base, "release")
        clear_pack(pid)
        out = os.path.join(OUT, pid)
        gain = min(peak_gain(os.path.join(press, "GENERIC_R%d.mp3" % r)) for r in range(5))
        rgain = peak_gain(os.path.join(rel, "GENERIC.mp3")) - 3.0
        keys = {}
        # One unpanned file per row; the mixer pans by key at playback (`pan: true`).
        for r, codes in ROWS.items():
            encode(os.path.join(press, "GENERIC_R%d.mp3" % r), os.path.join(out, "row%d.opus" % r),
                   0.0, 0.25, gain, None)
            encode(os.path.join(rel, "GENERIC.mp3"), os.path.join(out, "up", "row%d.opus" % r),
                   0.0, 0.2, rgain, None)
            for c in codes:
                if c not in SPECIAL:
                    keys[str(c)] = "row%d.opus" % r
        for c, nm in SPECIAL.items():
            for sub, g in (("press", gain), ("release", rgain)):
                src = os.path.join(base, sub, nm.upper() + ".mp3")
                if os.path.exists(src):
                    dst = os.path.join(out, "up" if sub == "release" else "", "%d.opus" % c)
                    encode(src, dst, 0.0, 0.25, g, None)
        link_default(out, "row3.opus")
        link_default(os.path.join(out, "up"), "row3.opus")
        write_pack(pid, {"name": name, "credit": "kbsim by Thomas Lai", "source": KBSIM,
                         "origin": "src/assets/audio/" + folder, "license": "MIT",
                         "release": "recorded", "stereo": True, "pan": True, "keys": keys})


MOUSE_KEYS = {"272": "left.opus", "273": "right.opus", "274": "middle.opus",
              "275": "middle.opus", "276": "middle.opus"}


def build_tt_mouse(root):
    for folder, pid, name, credit, split, end in TT_MOUSE:
        src_dir = os.path.join(root, "mouse-sounds", folder)
        rel = "mouse/" + pid
        clear_pack(rel)
        out = os.path.join(OUT, rel)
        gain = min(peak_gain(os.path.join(src_dir, b + ".wav")) for b in ("left", "right", "middle"))
        for b in ("left", "right", "middle"):
            src = os.path.join(src_dir, b + ".wav")
            encode(src, os.path.join(out, b + ".opus"), 0.0, split, gain)
            encode(src, os.path.join(out, "up", b + ".opus"), split, end - split, gain - 2.0)
        link_default(out, "left.opus")
        link_default(os.path.join(out, "up"), "left.opus")
        write_pack(rel, {"name": name, "credit": credit, "source": TYPETONE,
                         "origin": "mouse-sounds/" + folder, "license": "CC0 recording, MIT processing",
                         "keys": MOUSE_KEYS, "release": "recorded"})


def build_dx_mouse(root):
    for folder, pid, name in DX_MOUSE:
        src_dir = os.path.join(root, "soundpacks", "mouse", folder)
        cfg = json.load(open(os.path.join(src_dir, "config.json")))
        sprite = os.path.join(src_dir, cfg["audio_file"])
        rel = "mouse/" + pid
        clear_pack(rel)
        out = os.path.join(OUT, rel)
        gain = peak_gain(sprite)
        names = {"MouseLeft": "left", "MouseRight": "right", "MouseMiddle": "middle"}
        for kname, d in cfg["definitions"].items():
            b = names.get(kname)
            if not b:
                continue
            (p0, p1), (r0, r1) = d["timing"]
            encode(sprite, os.path.join(out, b + ".opus"), p0 / 1000, min(p1 - p0, 250) / 1000, gain)
            encode(sprite, os.path.join(out, "up", b + ".opus"), r0 / 1000, min(r1 - r0, 200) / 1000, gain - 2.0)
        if not os.path.exists(os.path.join(out, "middle.opus")):
            os.link(os.path.join(out, "left.opus"), os.path.join(out, "middle.opus"))
            os.link(os.path.join(out, "up", "left.opus"), os.path.join(out, "up", "middle.opus"))
        link_default(out, "left.opus")
        link_default(os.path.join(out, "up"), "left.opus")
        write_pack(rel, {"name": name, "credit": "Mechvibes", "source": MECHVIBES_DX,
                         "origin": "soundpacks/mouse/" + folder, "license": "MIT",
                         "keys": MOUSE_KEYS, "release": "recorded"})


def main(argv):
    if len(argv) != 5:
        print(__doc__)
        return 2
    mv, dx, kb, tt = argv[1:]
    os.makedirs(OUT, exist_ok=True)
    build_dx(dx)
    build_mv(mv)
    build_mv_multi(mv)
    build_kbsim(kb)
    build_tt_mouse(tt)
    build_dx_mouse(dx)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
