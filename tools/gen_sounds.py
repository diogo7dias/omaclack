#!/usr/bin/env python3
"""Generate the synthetic starter sound packs. Stdlib only, deterministic.

Usage: tools/gen_sounds.py [pack ...]     (default: all four packs)

Each pack: <keycode>.wav for a spread of common keys, default.wav, mouse.wav.
44.1 kHz, mono, 16-bit, every file under 80 ms.
"""

import math
import os
import random
import struct
import sys
import wave

RATE = 44100
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sounds")

# Keys that get their own file. Everything else falls back to default.wav.
KEYS = [1, 14, 15, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 42, 56, 57]
BIG_KEYS = {57: 0.78, 28: 0.86, 14: 0.9, 15: 0.94, 42: 0.9, 29: 0.92, 56: 0.92}  # pitch factor

# Per-pack synthesis recipe.
#   dur      total length in seconds (< 0.080)
#   noise    (decay_s, resonator_hz, resonator_r, gain)  coloured burst = the "click"
#   modes    [(hz, decay_s, gain), ...]                   damped sines = the "body"
#   click2   (delay_s, gain) or None                      second transient (click jacket)
#   attack   seconds of soft attack on the body
PACKS = {
    "cherry-blue": dict(
        dur=0.045,
        noise=(0.0015, 5200, 0.93, 1.0),
        modes=[(3800, 0.008, 0.55), (1900, 0.012, 0.25)],
        click2=(0.006, 0.6),
        attack=0.0002,
        peak=0.80,
    ),
    "cherry-brown": dict(
        dur=0.055,
        noise=(0.003, 1400, 0.90, 0.6),
        modes=[(900, 0.016, 0.7), (1600, 0.010, 0.35)],
        click2=None,
        attack=0.0006,
        peak=0.66,
    ),
    "topre": dict(
        dur=0.075,
        noise=(0.002, 900, 0.88, 0.45),
        modes=[(420, 0.032, 0.9), (780, 0.020, 0.45), (1300, 0.010, 0.2)],
        click2=None,
        attack=0.0012,
        peak=0.78,
    ),
    "typewrite": dict(
        dur=0.078,
        noise=(0.0018, 6500, 0.95, 1.0),
        modes=[(2900, 0.028, 0.5), (4300, 0.022, 0.4), (6100, 0.016, 0.3), (1100, 0.030, 0.35)],
        click2=(0.011, 0.5),
        attack=0.0002,
        peak=0.88,
    ),
}


def resonator(x, hz, r):
    """Two-pole bandpass. y[n] = 2r cos(w) y[n-1] - r^2 y[n-2] + x[n]."""
    w = 2 * math.pi * hz / RATE
    a1, a2 = 2 * r * math.cos(w), -(r * r)
    y1 = y2 = 0.0
    out = []
    for v in x:
        y = v + a1 * y1 + a2 * y2
        out.append(y)
        y2, y1 = y1, y
    return out


def synth(recipe, pitch, seed):
    rnd = random.Random(seed)
    n = int(recipe["dur"] * RATE)
    out = [0.0] * n

    def add_click(start_s, gain):
        decay, hz, r, g = recipe["noise"]
        start = int(start_s * RATE)
        length = min(n - start, int(decay * 6 * RATE) + 64)
        burst = [(rnd.random() * 2 - 1) * math.exp(-i / (decay * RATE)) for i in range(length)]
        burst = resonator(burst, hz * pitch, r)
        for i, v in enumerate(burst):
            out[start + i] += v * g * gain * 0.35

    add_click(0.0, 1.0)
    if recipe["click2"]:
        d, g = recipe["click2"]
        add_click(d, g)

    attack = max(1, int(recipe["attack"] * RATE))
    for hz, decay, g in recipe["modes"]:
        f = hz * pitch * (1 + (rnd.random() - 0.5) * 0.01)
        ph = rnd.random() * 2 * math.pi
        for i in range(n):
            env = math.exp(-i / (decay * RATE))
            if i < attack:
                env *= i / attack
            out[i] += g * env * math.sin(2 * math.pi * f * i / RATE + ph)

    # Fade the last 3 ms so no file ends on a click.
    fade = int(0.003 * RATE)
    for i in range(fade):
        out[n - fade + i] *= 1 - i / fade

    peak = max(abs(v) for v in out) or 1.0
    scale = recipe["peak"] / peak
    return [max(-1.0, min(1.0, v * scale)) for v in out]


def write_wav(path, samples):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(v * 32767)) for v in samples))


def pitch_for(code):
    if code in BIG_KEYS:
        return BIG_KEYS[code]
    # Stable per-key spread of +-6 % so a row of letters does not sound like one sample looping.
    return 0.94 + ((code * 2654435761) % 1000) / 1000.0 * 0.12


def build(pack):
    recipe = PACKS[pack]
    d = os.path.join(OUT, pack)
    os.makedirs(d, exist_ok=True)
    for code in KEYS:
        write_wav(os.path.join(d, "%d.wav" % code), synth(recipe, pitch_for(code), hash_seed(pack, code)))
    write_wav(os.path.join(d, "default.wav"), synth(recipe, 1.0, hash_seed(pack, -1)))
    # Mouse: quieter, higher, shorter tail.
    m = synth(recipe, 1.25, hash_seed(pack, -2))[: int(recipe["dur"] * 0.7 * RATE)]
    fade = int(0.004 * RATE)
    for i in range(fade):
        m[len(m) - fade + i] *= 1 - i / fade
    write_wav(os.path.join(d, "mouse.wav"), [v * 0.55 for v in m])


def hash_seed(pack, code):
    return sum(ord(c) for c in pack) * 1000 + code + 7


if __name__ == "__main__":
    targets = sys.argv[1:] or list(PACKS)
    for p in targets:
        build(p)
        print("built", p)
