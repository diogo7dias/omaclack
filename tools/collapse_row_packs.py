#!/usr/bin/env python3
"""Collapse kbsim per-key copies into one file per row + pan-at-playback.

Shipped kbsim packs duplicated a row sample once per key so each key could be
panned at encode time. The Rust mixer now pans, so each pack only needs the
five row samples, space/enter/backspace, and default.

Usage: tools/collapse_row_packs.py [sounds-dir]
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_sounds as B

PACKS = [pid for _, pid, _ in B.KB_PACKS]
SPECIAL = B.SPECIAL  # 14 backspace, 28 enter, 57 space
KEEP_CODES = set(SPECIAL)


def center_code(codes):
    return codes[len(codes) // 2]


def collapse_dir(d, keys):
    """Copy row centres to rowN.opus, keep specials, delete the rest."""
    if not os.path.isdir(d):
        return 0
    for r, codes in B.ROWS.items():
        src_code = None
        for c in [center_code(codes)] + list(codes):
            p = os.path.join(d, "%d.opus" % c)
            if os.path.isfile(p):
                src_code = c
                break
        if src_code is None:
            continue
        src = os.path.join(d, "%d.opus" % src_code)
        dst = os.path.join(d, "row%d.opus" % r)
        if os.path.abspath(src) != os.path.abspath(dst):
            shutil.copy2(src, dst)
        for c in codes:
            if c not in KEEP_CODES:
                keys[str(c)] = "row%d.opus" % r
    for fn in os.listdir(d):
        stem, ext = os.path.splitext(fn)
        if ext != ".opus" or not stem.isdigit():
            continue
        code = int(stem)
        if code in KEEP_CODES:
            continue
        os.remove(os.path.join(d, fn))
    pick = "row3.opus" if os.path.isfile(os.path.join(d, "row3.opus")) else None
    if pick:
        default = os.path.join(d, "default.opus")
        try:
            os.remove(default)
        except FileNotFoundError:
            pass
        os.link(os.path.join(d, pick), default)
    return sum(1 for fn in os.listdir(d) if fn.endswith(".opus"))


def collapse_pack(root, pid):
    d = os.path.join(root, pid)
    meta = json.load(open(os.path.join(d, "pack.json")))
    keys = {}
    n = collapse_dir(d, keys)
    n += collapse_dir(os.path.join(d, "up"), keys)
    meta["keys"] = keys
    meta["pan"] = True
    B.write_pack(pid, meta)
    return n


def main(argv):
    root = argv[1] if len(argv) > 1 else B.OUT
    B.OUT = root
    total = 0
    for pid in PACKS:
        n = collapse_pack(root, pid)
        total += n
        print("%-20s %3d files" % (pid, n))
    print("total", total)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
