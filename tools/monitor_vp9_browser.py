"""VP9 plan, phase V5: record what the hardware VP9 decoder does while browsers play video.

Samples every few seconds, until stopped (Ctrl-C, SIGTERM or the file <out>/stop):
- NRedVP9State from the registry and per-frame counters from older builds when available;
- the CPU time of VTDecoderXPCService and of the browsers' GPU, renderer and main processes;
- gaps between samples longer than a minute (sleep).
Streams VTDecoderXPCService errors, faults and VP9 messages, and kernel messages about NootedRed, the VCN or GPU
restarts, to <out>/log.ndjson. `--summary` prints the analysis of a finished recording.

Usage: python3 tools/monitor_vp9_browser.py [--out build/vp9-browser] [--interval 5]
       python3 tools/monitor_vp9_browser.py --summary [--out build/vp9-browser]
"""
import argparse
import csv
import json
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Trace codes (UserVideoDecode): 1 create, 7 bitstream, 8 message, 9 DecodePicture entry, 10 DecodePicture refusal,
# 11 VADecoder::DecodePicture result.
CODES = [1, 7, 8, 9, 10, 11]
GROUPS = {
    "vtdecoder": lambda c: c.endswith("/VTDecoderXPCService"),
    "chrome_gpu": lambda c: "Chrome Helper (GPU)" in c,
    "chrome_renderer": lambda c: "Chrome Helper (Renderer)" in c,
    "chrome": lambda c: c.endswith("/Google Chrome"),
    "webkit_gpu": lambda c: "com.apple.WebKit.GPU" in c,
    "webkit_content": lambda c: "com.apple.WebKit.WebContent" in c,
    "safari": lambda c: c.endswith("/Safari"),
}
LOG_PREDICATE = (
    '(process == "VTDecoderXPCService" AND (messageType == error OR messageType == fault OR eventMessage CONTAINS "VP9"))'
    ' OR (process == "kernel" AND (eventMessage CONTAINS[c] "NootedRed" OR eventMessage CONTAINS[c] "NRed"'
    ' OR eventMessage CONTAINS[c] "VCN" OR eventMessage CONTAINS[c] "GPU Restart" OR eventMessage CONTAINS[c] "AMDRadeon"))'
)


def registry():
    text = subprocess.run(["ioreg", "-lw0"], capture_output=True, text=True).stdout
    values = {k: int(v) for k, v in re.findall(r'"(NRedVP9(?:T\d+[NV]|State))" = (\d+)', text)}
    return {f"T{c:02d}{s}": values.get(f"NRedVP9T{c:02d}{s}", 0) for c in CODES for s in "NV"} | {
        "state": values.get("NRedVP9State", 0)}


def seconds(field):
    days, _, rest = field.rpartition("-")
    total = 0.0
    for part in rest.split(":"):
        total = total * 60 + float(part)
    return total + (int(days) * 86400 if days else 0)


def processes():
    out = subprocess.run(["ps", "-axo", "pid=,time=,comm="], capture_output=True, text=True).stdout
    found = {}
    for line in out.splitlines():
        pid, cpu, command = line.strip().split(None, 2)
        for group, match in GROUPS.items():
            if match(command):
                found[int(pid)] = (group, seconds(cpu))
    return found


def record(out, interval):
    out.mkdir(parents=True, exist_ok=True)
    (out / "stop").unlink(missing_ok=True)
    log = subprocess.Popen(["/usr/bin/log", "stream", "--style", "ndjson", "--level", "default", "--predicate",
                            LOG_PREDICATE], stdout=open(out / "log.ndjson", "a"), stderr=subprocess.DEVNULL)
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(1))
    baseline = processes()    # CPU used before the recording is not counted
    cpu = {pid: (group, 0.0) for pid, (group, _) in baseline.items()}
    fields = ["time", "gap", "state"] + [f"T{c:02d}{s}" for c in CODES for s in "NV"] + [f"cpu_{g}" for g in GROUPS]
    new = not (out / "samples.csv").exists()
    with open(out / "samples.csv", "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if new:
            writer.writeheader()
        last = time.time()
        print(f"recording to {out}; stop with Ctrl-C or: touch {out / 'stop'}", flush=True)
        try:
            while not stopping and not (out / "stop").exists():
                now = time.time()
                for pid, (group, used) in processes().items():
                    cpu[pid] = (group, used - baseline.get(pid, (group, 0.0))[1])
                totals = {f"cpu_{g}": round(sum(v for k, v in cpu.values() if k == g), 2) for g in GROUPS}
                writer.writerow({"time": round(now, 1), "gap": round(now - last, 1)} | registry() | totals)
                f.flush()
                last = now
                time.sleep(interval)
        except KeyboardInterrupt:
            pass
    log.terminate()
    print("stopped")


def summary(out):
    rows = list(csv.DictReader(open(out / "samples.csv")))
    if len(rows) < 2:
        print("not enough samples")
        return
    first, last = rows[0], rows[-1]
    delta = lambda key: int(last[key]) - int(first[key])
    span = float(last["time"]) - float(first["time"])
    gaps = [r for r in rows[1:] if float(r["gap"]) > 60]
    print(f"recording: {span / 60:.1f} min, {len(rows)} samples, NRedVP9State {first['state']} -> {last['state']}")
    print(f"sleep or stall gaps over 60 s: {len(gaps)}" +
          "".join(f"\n  {time.strftime('%H:%M:%S', time.localtime(float(r['time'])))}: {float(r['gap']):.0f} s"
                  for r in gaps))
    if any(int(row.get("T09N") or 0) for row in rows):
        print(f"VP9 sessions requested: {delta('T01N')}, frames into DecodePicture: {delta('T09N')}, "
              f"refused by DecodePicture: {delta('T10N')}")
        failures = sorted({int(r["T11V"]) for r in rows if int(r["T11V"])})
        print(f"VADecoder::DecodePicture failure values seen in samples: {failures or 'none'}")
        # T08 is the frame size (width << 16 | height) on success, a code below 0x10000 on failure.
        sizes = sorted({int(r["T08V"]) for r in rows[1:] if int(r["T08V"]) >= 0x10000})
        print("decoded frame sizes seen in samples: " +
              (", ".join(f"{v >> 16}x{v & 0xFFFF}" for v in sizes) if sizes else "none"))
    print("CPU seconds during the recording:")
    for group in GROUPS:
        used = float(last[f"cpu_{group}"])
        if used:
            print(f"  {group:16s} {used:8.1f} s  ({100 * used / span:.1f}% of one core)")
    errors, kernel, vp9 = [], [], 0
    if (out / "log.ndjson").exists():
        for line in open(out / "log.ndjson", errors="replace"):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            message = entry.get("eventMessage", "")
            if entry.get("processImagePath", "").endswith("kernel"):
                kernel.append(message)
            elif entry.get("messageType") in ("Error", "Fault"):
                errors.append(message)
            elif "VP9" in message:
                vp9 += 1
    print(f"VTDecoderXPCService VP9 messages: {vp9}, errors/faults: {len(errors)}, kernel messages: {len(kernel)}")
    for message in sorted(set(errors))[:15]:
        print(f"  error x{errors.count(message)}: {message[:160]}")
    for message in sorted(set(kernel))[:15]:
        print(f"  kernel x{kernel.count(message)}: {message[:160]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "build/vp9-browser")
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    if args.summary:
        summary(args.out)
    else:
        record(args.out, args.interval)


if __name__ == "__main__":
    sys.exit(main())
