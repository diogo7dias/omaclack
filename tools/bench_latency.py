#!/usr/bin/env python3
"""Playback-strategy latency benchmark.

Usage: tools/bench_latency.py [N] [candidate]
  candidate: "stream-blocking (old mixer)" | "stream-paced" | "stream-paced+4k-pipe"

Plays N clicks each via the current one-shot pw-play path and the candidate
persistent-stream path into a private null sink, records the sink monitor with
parecord, and reports onset-minus-trigger per strategy relative to the fastest.
The absolute offset is unknown (recording clock); only the difference matters.
Mutes the running daemon while it runs. Results on the author's machine:
one-shot == paced stream (within 2 ms); blocking stream is ~770 ms late
(the 64 KiB pipe fills with silence ahead of pw-play).
"""
import array, fcntl, io, math, os, statistics, subprocess, sys, threading, time, wave, socket, json

RATE = 44100
REC_RATE = 48000
MON = "stbench.monitor"
SINK = "stbench"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 8
GAP = 2.5
CHUNK = 256
F_SETPIPE_SZ = 1031

# 20 ms 1 kHz burst, hard onset
burst = array.array("h", [int(20000 * math.sin(2 * math.pi * 1000 * i / RATE)) for i in range(int(0.02 * RATE))])
def wav_bytes(s):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(s.tobytes())
    return b.getvalue()
WAV = wav_bytes(burst)

# --- strategy A: one-shot pw-play per click (current daemon)
def oneshot():
    p = subprocess.Popen(["pw-play", "--target", SINK, "-"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p.stdin.write(WAV); p.stdin.close(); p.wait()

# --- strategy B/C: persistent raw stream
class Stream(threading.Thread):
    def __init__(self, paced, small_pipe, latency=CHUNK):
        super().__init__(daemon=True)
        self.paced, self.small_pipe = paced, small_pipe
        self.lock = threading.Lock(); self.voices = []; self.stop = threading.Event()
        self.proc = subprocess.Popen(["pw-play", "--target", SINK, "--raw", "--format=s16", "--rate=%d" % RATE, "--channels=1",
                                      "--latency=%d/%d" % (latency, RATE), "-"],
                                     stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, bufsize=0)
        if small_pipe:
            fcntl.fcntl(self.proc.stdin.fileno(), F_SETPIPE_SZ, 4096)
    def trigger(self):
        with self.lock: self.voices.append([burst, 0])
    def run(self):
        silence = bytes(CHUNK * 2)
        t = time.monotonic(); period = CHUNK / RATE
        while not self.stop.is_set():
            with self.lock:
                if self.voices:
                    out = [0] * CHUNK
                    for v in self.voices:
                        s, pos = v; n = min(CHUNK, len(s) - pos)
                        for i in range(n): out[i] += s[pos + i]
                        v[1] = pos + n
                    self.voices = [v for v in self.voices if v[1] < len(v[0])]
                    chunk = array.array("h", [max(-32768, min(32767, x)) for x in out]).tobytes()
                else:
                    chunk = silence
            try: self.proc.stdin.write(chunk)
            except (BrokenPipeError, OSError): return
            if self.paced:
                t += period
                d = t - time.monotonic()
                if d > 0: time.sleep(d)
                elif d < -0.05: t = time.monotonic()  # fell behind badly; resync
    def close(self):
        self.stop.set(); time.sleep(0.05)
        try: self.proc.stdin.close(); self.proc.terminate()
        except OSError: pass

def ctl(msg):
    s = socket.socket(socket.AF_UNIX); s.connect(os.environ["XDG_RUNTIME_DIR"] + "/soundtap/ctl.sock")
    s.sendall((json.dumps(msg) + "\n").encode()); s.recv(4096); s.close()

def main():
    mod = subprocess.check_output(["pactl", "load-module", "module-null-sink", "sink_name=" + SINK]).decode().strip()
    time.sleep(0.3)
    try: ctl({"cmd": "mute", "toggle": True})  # silence the real daemon while we type/bench
    except OSError: pass
    try:
        rec = subprocess.Popen(["parecord", "--device=" + MON, "--raw", "--format=s16le", "--rate=%d" % REC_RATE,
                                "--channels=1", "--latency-msec=10"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        buf = io.BytesIO()
        def pump():
            while True:
                d = rec.stdout.read(4096)
                if not d: break
                buf.write(d)
        threading.Thread(target=pump, daemon=True).start()
        time.sleep(0.5)

        allstreams = {
            "stream-blocking (old mixer)": lambda: Stream(paced=False, small_pipe=False),
            "stream-paced": lambda: Stream(paced=True, small_pipe=False),
            "stream-paced+4k-pipe": lambda: Stream(paced=True, small_pipe=True),
        }
        cand = sys.argv[2] if len(sys.argv) > 2 else "stream-paced"
        streams = {cand: allstreams[cand]()}
        for s in streams.values(): s.start()
        time.sleep(1.0)  # let streams settle (blocking one fills its pipe here)

        strategies = [("one-shot (current)", lambda: threading.Thread(target=oneshot, daemon=True).start())]
        strategies += [(k, v.trigger) for k, v in streams.items()]

        triggers = []
        for i in range(N):
            for name, fn in strategies:
                t0 = time.monotonic(); fn(); triggers.append((name, t0)); time.sleep(GAP)
        time.sleep(1.5)
        for s in streams.values(): s.close()
        rec.send_signal(2); rec.wait(); time.sleep(0.2)
        t_end = time.monotonic()
    finally:
        subprocess.call(["pkill", "-x", "pw-play"])
        subprocess.call(["pactl", "unload-module", mod])
        try: ctl({"cmd": "mute", "toggle": False})
        except OSError: pass

    pcm = array.array("h", buf.getvalue()[: len(buf.getvalue()) // 2 * 2])
    # recording end aligns (roughly) with t_end; onsets are located relative to the start
    onsets = []; i = 0; thr = 3000; refractory = int(0.15 * REC_RATE)
    while i < len(pcm):
        if abs(pcm[i]) > thr:
            onsets.append(i / REC_RATE); i += refractory
        else: i += 1
    print("triggers %d, onsets %d" % (len(triggers), len(onsets)))
    # align: recording ends at ~t_end
    rec_len = len(pcm) / REC_RATE
    rec_start = t_end - rec_len
    rows = {}
    for name, t0 in triggers:
        rel = t0 - rec_start
        nxt = [o for o in onsets if o >= rel - 0.02]
        if not nxt or nxt[0] - rel > GAP * 0.9:
            rows.setdefault(name, []).append(float("nan")); continue
        rows.setdefault(name, []).append(nxt[0] - rel)
    for k, v in rows.items():
        print("%-30s raw offsets ms: %s" % (k, [round(x * 1000) if x == x else None for x in v]))
    rows = {k: [x for x in v if x == x] for k, v in rows.items()}
    strategies = [(k, None) for k in rows if rows[k]]
    base = min(statistics.median(v) for v in rows.values())
    print("\n%-30s %8s %8s %8s   (ms, relative to fastest median)" % ("strategy", "median", "min", "stdev"))
    for name, _ in strategies:
        v = [(x - base) * 1000 for x in rows[name]]
        print("%-30s %8.1f %8.1f %8.1f" % (name, statistics.median(v), min(v), statistics.pstdev(v)))
if __name__ == "__main__":
    main()
