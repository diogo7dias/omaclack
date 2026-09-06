#!/usr/bin/env python3
"""Measure a daemon build: startup, idle footprint, spawn latency, audio onset.

Usage: tools/bench_daemon.py <label> <daemon-command...>
  e.g. tools/bench_daemon.py python python3 bin/omaclackd
       tools/bench_daemon.py rust   target/release/omaclackd

Runs the daemon with a private socket and stdin pipe (as the shell does),
routes audio to a temporary null sink, and reports:
  startup_ms   spawn -> hello line on stdout
  rss_kb       resident set after 2 s idle
  idle_cpu_ms  CPU time consumed over a 10 s idle stretch (should be ~0)
  cmd_ms       socket 'play' round trip, p50/p95, N=60 (spawn cost incl. IPC)
  onset_ms     'play' sent -> first sample on the sink monitor, p50/p95, N=20
               (includes the capture path, same for every backend)
  onset_cold   the same, for the first key after 4 s of silence, N=5
Prints one JSON line so runs can be diffed.
"""
import json
import os
import struct
import subprocess
import sys
import tempfile
import time

N_CMD, N_ONSET, GAP = 60, 20, 0.35
N_COLD, COLD_GAP = 5, 4.0


def cpu_ms(pid):
    with open("/proc/%d/stat" % pid) as f:
        parts = f.read().rsplit(")", 1)[1].split()
    return (int(parts[11]) + int(parts[12])) * 1000.0 / os.sysconf("SC_CLK_TCK")


def rss_kb(pid):
    with open("/proc/%d/status" % pid) as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return 0


def rpc(p, obj):
    p.stdin.write((json.dumps(obj) + "\n").encode())
    p.stdin.flush()
    while True:
        line = json.loads(p.stdout.readline())
        if line.get("evt") in ("key", "theme"):
            continue
        return line


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 2)


def main(argv):
    label, cmd = argv[1], argv[2:]
    sink = "omabench"
    subprocess.run(["pactl", "load-module", "module-null-sink", "sink_name=" + sink], capture_output=True)
    default_sink = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True).stdout.strip()
    subprocess.run(["pactl", "set-default-sink", sink], capture_output=True)
    sock = os.path.join(tempfile.mkdtemp(prefix="omabench-"), "ctl.sock")
    res = {"label": label}
    try:
        t0 = time.monotonic()
        p = subprocess.Popen(cmd + ["--socket=" + sock], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
        hello = json.loads(p.stdout.readline())
        res["startup_ms"] = round((time.monotonic() - t0) * 1000, 1)
        assert hello["evt"] == "hello"
        rpc(p, {"cmd": "load", "pack": "mx-blue"})
        rpc(p, {"cmd": "release", "value": False})
        rpc(p, {"cmd": "volume", "value": 100})
        time.sleep(2.0)
        res["rss_kb"] = rss_kb(p.pid)
        c0 = cpu_ms(p.pid)
        time.sleep(10.0)
        res["idle_cpu_ms"] = round(cpu_ms(p.pid) - c0, 1)

        lat = []
        for i in range(N_CMD):
            t = time.monotonic()
            r = rpc(p, {"cmd": "play", "key": 30 + (i % 10)})
            lat.append((time.monotonic() - t) * 1000)
            time.sleep(0.12)
        res["cmd_ms_p50"], res["cmd_ms_p95"] = pct(lat, 0.5), pct(lat, 0.95)
        time.sleep(1.0)

        # Onset: record the null sink monitor, fire plays at known times, find bursts.
        rec = subprocess.Popen(["parecord", "--raw", "--format=s16le", "--rate=48000", "--channels=1",
                                "--latency-msec=10", "-d", sink + ".monitor"], stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)
        import fcntl
        fl = fcntl.fcntl(rec.stdout, fcntl.F_GETFL)
        fcntl.fcntl(rec.stdout, fcntl.F_SETFL, fl | os.O_NONBLOCK)
        buf = bytearray()
        epoch = [None]   # wall-clock time of sample 0, anchored on the first chunk

        def pump():
            try:
                while True:
                    chunk = rec.stdout.read(65536)
                    if not chunk:
                        break
                    if epoch[0] is None:
                        epoch[0] = time.monotonic() - len(chunk) / 2 / 48000.0
                    buf.extend(chunk)
            except (BlockingIOError, TypeError):
                pass

        while epoch[0] is None:
            pump()
            time.sleep(0.01)
        time.sleep(0.5)
        marks = []
        for i in range(N_ONSET + N_COLD):
            pump()
            if i >= N_ONSET:
                # Cold: let the daemon go idle first (in-process backend suspends its stream).
                t_end = time.monotonic() + COLD_GAP
                while time.monotonic() < t_end:
                    pump()
                    time.sleep(0.05)
            marks.append(time.monotonic())
            rpc(p, {"cmd": "play", "key": 57})
            time.sleep(GAP)
        time.sleep(0.6)
        pump()
        rec.terminate()
        n = len(buf) // 2
        samples = struct.unpack("<%dh" % n, bytes(buf[:n * 2]))
        onsets, i, thr = [], 0, 1500
        while i < n:
            if abs(samples[i]) > thr:
                onsets.append(epoch[0] + i / 48000.0)
                i += int(0.15 * 48000)
            else:
                i += 1
        res["onset_found"] = len(onsets)
        if len(onsets) >= N_ONSET + N_COLD:
            d = [(o - m) * 1000 for o, m in zip(onsets[:N_ONSET], marks[:N_ONSET])]
            # Absolute: command sent -> first sample on the sink monitor, including the
            # capture path (identical for every backend, so differences are real).
            res["onset_ms_p50"], res["onset_ms_p95"] = pct(d, 0.5), pct(d, 0.95)
            res["onset_jitter_ms"] = round(pct(d, 0.95) - min(d), 2)
            cold = [(o - m) * 1000 for o, m in zip(onsets[N_ONSET:N_ONSET + N_COLD], marks[N_ONSET:])]
            res["onset_cold_ms_p50"] = pct(cold, 0.5)   # first key after 4 s of silence
        rpc(p, {"cmd": "quit"})
        p.wait(3)
    finally:
        subprocess.run(["pactl", "set-default-sink", default_sink], capture_output=True)
        mods = subprocess.run(["pactl", "list", "short", "modules"], capture_output=True, text=True).stdout
        for line in mods.splitlines():
            if "sink_name=" + sink in line:
                subprocess.run(["pactl", "unload-module", line.split()[0]], capture_output=True)
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
