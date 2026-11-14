"""VP9 plan, phase V3: compare the hardware decode of a clip with ffmpeg, frame by frame, luma and chroma apart.

Runs tools/probe_vp9_decode.m (built at build/vp9/probe_vp9_decode) on each clip with VP9_DUMP pointing at a FIFO, and
ffmpeg -pix_fmt nv12 into a pipe, so long clips need no multi-GB dumps. The clip size must not change mid-stream.

Usage: python3 tools/compare_vp9_decode.py clip.ivf [clip.ivf ...] [--timeout seconds]
"""
import argparse
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "build/vp9/probe_vp9_decode"


def read_exactly(stream, size):
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            break
        data += chunk
    return bytes(data)


def psnr(a, b):
    mse = sum((x - y) ** 2 for x, y in zip(a, b)) / len(a)
    return 99.0 if mse == 0 else 10 * math.log10(255 * 255 / mse)


def compare(clip, timeout):
    size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                           "-of", "csv=p=0", clip], capture_output=True, text=True, check=True).stdout.strip()
    width, height = map(int, size.split(","))
    luma = width * height
    frame_size = luma + ((width + 1) // 2) * ((height + 1) // 2) * 2
    with tempfile.TemporaryDirectory() as work:
        fifo = os.path.join(work, "hw.nv12")
        os.mkfifo(fifo)
        log = open(os.path.join(work, "probe.txt"), "w+")
        probe = subprocess.Popen([str(PROBE), clip], env=dict(os.environ, VP9_DUMP=fifo), stdout=log,
                                 stderr=subprocess.STDOUT)
        start = time.time()

        def watchdog():
            while probe.poll() is None:
                if time.time() - start > timeout:
                    probe.kill()
                    return
                time.sleep(0.5)

        threading.Thread(target=watchdog, daemon=True).start()
        # Open the FIFO without blocking: a probe that fails before opening it must not hang this script.
        while True:
            try:
                fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
                break
            except OSError:
                if probe.poll() is not None:
                    fd = None
                    break
                time.sleep(0.05)
        reference = subprocess.Popen(["ffmpeg", "-v", "error", "-i", clip, "-f", "rawvideo", "-pix_fmt", "nv12", "-"],
                                     stdout=subprocess.PIPE)
        hw = None
        if fd is not None:
            os.set_blocking(fd, True)
            hw = os.fdopen(fd, "rb")
        frames = same_luma = same_chroma = 0
        diffs = []
        while hw:
            ref = read_exactly(reference.stdout, frame_size)
            out = read_exactly(hw, frame_size)
            if len(ref) < frame_size or len(out) < frame_size:
                break
            same_luma += out[:luma] == ref[:luma]
            same_chroma += out[luma:] == ref[luma:]
            if out != ref and len(diffs) < 3:
                diffs.append(f"{frames}: Y {psnr(out[:luma], ref[:luma]):.1f} dB, CbCr "
                             f"{psnr(out[luma:], ref[luma:]):.1f} dB")
            frames += 1
        if hw:
            hw.close()
        reference.stdout.close()
        reference.kill()
        probe.wait()
        log.seek(0)
        text = log.read()
    create = re.search(r"VTDecompressionSessionCreate = (-?\d+)", text)
    result = re.search(r"decoded ok (\d+), bad (\d+)", text)
    summary = (f"{Path(clip).name} {width}x{height}: create {create.group(1) if create else '?'}, "
               f"ok {result.group(1) if result else '?'} bad {result.group(2) if result else '?'}, "
               f"compared {frames}, Y identical {same_luma}, CbCr identical {same_chroma}, "
               f"{time.time() - start:.0f} s")
    if diffs:
        summary += " | first differences " + "; ".join(diffs)
    identical = bool(create and create.group(1) == "0" and result and result.group(2) == "0" and frames and
                     same_luma == frames == same_chroma == int(result.group(1)))
    return identical, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("clips", nargs="+")
    parser.add_argument("--timeout", type=float, default=300, help="seconds before the probe is killed")
    args = parser.parse_args()
    failed = 0
    for clip in args.clips:
        identical, summary = compare(clip, args.timeout)
        failed += not identical
        print(("PASS " if identical else "FAIL ") + summary, flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
