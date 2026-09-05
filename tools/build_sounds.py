#!/usr/bin/env python3
"""Import sound packs from Mechvibes and kbsim into sounds/<pack>/.

Usage: tools/build_sounds.py <mechvibes-checkout> <kbsim-checkout>

Output per pack: one Opus file per key (mono, 48 kHz, ~1.5 KB each), a
default.opus fallback, and pack.json with the display name and credit.
Mechvibes "single" packs are sliced from their sprite using config.json;
kbsim packs carry five row samples plus space/enter/backspace, which are
mapped onto keycodes with a small keys table in pack.json.
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "sounds")

MECHVIBES = "https://github.com/hainguyents13/mechvibes"
KBSIM = "https://github.com/tplai/kbsim"

# (folder in mechvibes/src/audio, our id, display name)
MV_PACKS = [
    ("cherrymx-blue-abs", "mx-blue", "Cherry MX Blue"),
    ("cherrymx-brown-abs", "mx-brown", "Cherry MX Brown"),
    ("cherrymx-red-abs", "mx-red", "Cherry MX Red"),
    ("cherrymx-black-abs", "mx-black", "Cherry MX Black"),
    ("topre-purple-hybrid-pbt", "topre", "Topre"),
]
# (folder in kbsim/src/assets/audio, our id, display name)
KB_PACKS = [
    ("holypanda", "holy-panda", "Holy Panda"),
    ("buckling", "buckling-spring", "Buckling Spring"),
    ("boxnavy", "box-navy", "Kailh Box Navy"),
    ("bluealps", "alps-blue", "Alps Blue"),
]

# Mechvibes keys are iohook raw codes. Below 0x100 they equal Linux KEY_*.
# Extended (0xE0-prefixed) scan codes appear as 0xE00|sc or 0xE000|sc.
EXT = {0x1C: 96, 0x1D: 97, 0x35: 98, 0x37: 99, 0x38: 100, 0x47: 102, 0x48: 103,
       0x49: 104, 0x4B: 105, 0x4D: 106, 0x4F: 107, 0x50: 108, 0x51: 109,
       0x52: 110, 0x53: 111, 0x5B: 125, 0x5C: 126, 0x5D: 127}


def linux_code(raw):
    if not str(raw).isdigit():      # "14-up" style release entries
        return None
    raw = int(raw)
    if raw < 0x100:
        return raw
    for base in (0xE00, 0xE000):
        if raw & ~0xFF == base and (raw & 0xFF) in EXT:
            return EXT[raw & 0xFF]
    return None


# Keyboard rows for kbsim's five generic samples.
ROWS = {
    0: [1] + list(range(59, 69)) + [87, 88],                         # esc, F1-F12
    1: list(range(2, 15)) + [41],                                     # 1..= backspace `
    2: [15] + list(range(16, 28)) + [43],                             # tab q..] backslash
    3: [58] + list(range(30, 41)) + [28],                             # caps a..' enter
    4: [42] + list(range(44, 54)) + [54, 29, 125, 56, 57, 100, 126, 127, 97],
}
SPECIAL = {14: "backspace", 28: "enter", 57: "space"}


def peak_gain(src):
    """Gain that brings the file's peak to -1 dBFS (ffmpeg volumedetect)."""
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", src, "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"max_volume: (-?[\d.]+) dB", r.stderr)
    return (-1.0 - float(m.group(1))) if m else 0.0


def encode(src, dst, start=None, dur=None, gain_db=0.0):
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if start is not None:
        cmd += ["-ss", "%.3f" % start, "-t", "%.3f" % dur]
    cmd += ["-i", src, "-ac", "1", "-ar", "48000",
            "-af", "volume=%.2fdB,afade=t=out:st=%.3f:d=0.02" % (gain_db, max(0.0, (dur or 0.2) - 0.02)),
            "-c:a", "libopus", "-b:a", "40k", "-vbr", "on", "-application", "audio", dst]
    subprocess.run(cmd, check=True)


def write_pack(pid, meta, files_written):
    with open(os.path.join(OUT, pid, "pack.json"), "w") as f:
        json.dump(meta, f, indent=2, sort_keys=True)
        f.write("\n")
    print("%-16s %3d files" % (pid, files_written))


def build_mechvibes(root):
    for folder, pid, name in MV_PACKS:
        src_dir = os.path.join(root, "src", "audio", folder)
        cfg = json.load(open(os.path.join(src_dir, "config.json")))
        sprite = os.path.join(src_dir, cfg["sound"])
        out = os.path.join(OUT, pid)
        os.makedirs(out, exist_ok=True)
        for f in os.listdir(out):
            os.remove(os.path.join(out, f))
        gain = peak_gain(sprite)
        n = 0
        for raw, span in cfg["defines"].items():
            code = linux_code(raw)
            if code is None or not isinstance(span, list):
                continue
            start_ms, dur_ms = span
            dur = min(dur_ms, 250) / 1000.0
            encode(sprite, os.path.join(out, "%d.opus" % code), start_ms / 1000.0, dur, gain)
            n += 1
        # Fallback for keys the recording does not cover: the 'a' key, else any.
        pick = "30.opus" if os.path.exists(os.path.join(out, "30.opus")) else sorted(os.listdir(out))[0]
        os.link(os.path.join(out, pick), os.path.join(out, "default.opus"))
        write_pack(pid, {
            "name": name,
            "credit": "Mechvibes",
            "source": MECHVIBES,
            "origin": "src/audio/" + folder,
            "license": "MIT",
        }, n + 1)


def build_kbsim(root):
    for folder, pid, name in KB_PACKS:
        src_dir = os.path.join(root, "src", "assets", "audio", folder, "press")
        out = os.path.join(OUT, pid)
        os.makedirs(out, exist_ok=True)
        for f in os.listdir(out):
            os.remove(os.path.join(out, f))
        gain = min(peak_gain(os.path.join(src_dir, "GENERIC_R%d.mp3" % r)) for r in range(5))
        keys = {}
        for r, codes in ROWS.items():
            encode(os.path.join(src_dir, "GENERIC_R%d.mp3" % r), os.path.join(out, "row%d.opus" % r), gain_db=gain)
            for c in codes:
                keys[str(c)] = "row%d.opus" % r
        for c, nm in SPECIAL.items():
            src = os.path.join(src_dir, nm.upper() + ".mp3")
            if os.path.exists(src):
                encode(src, os.path.join(out, nm + ".opus"), gain_db=gain)
                keys[str(c)] = nm + ".opus"
        os.link(os.path.join(out, "row3.opus"), os.path.join(out, "default.opus"))
        write_pack(pid, {
            "name": name,
            "credit": "kbsim by Thomas Lai",
            "source": KBSIM,
            "origin": "src/assets/audio/" + folder,
            "license": "MIT",
            "keys": keys,
        }, len(os.listdir(out)))


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    os.makedirs(OUT, exist_ok=True)
    build_mechvibes(argv[1])
    build_kbsim(argv[2])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
