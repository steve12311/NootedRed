"""用合成帧定位 VideoToolbox 编码失败阶段；硬件测试需显式启用，每项限时。"""
import argparse
import datetime
import json
import re
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


class HardwareFault(RuntimeError):
    pass


class GPUMonitor:
    """实时保存 GPU/桌面异常；监视器失效也停止后续硬件用例。"""
    def __init__(self, work):
        self.path = work / 'gpu-events.jsonl'
        self.output = self.path.open('w')
        self.errors = (work / 'gpu-monitor.stderr.txt').open('w')
        predicate = ('process == "kernel" AND (eventMessage CONTAINS[c] "gpuRestart"'
                     ' OR eventMessage CONTAINS[c] "GPU Reset") OR eventMessage CONTAINS[c] "WindowServer watchdog"')
        self.process = subprocess.Popen(['/usr/bin/log', 'stream', '--style', 'json', '--predicate', predicate],
                                        stdout=self.output, stderr=self.errors)

    def check(self):
        if self.process.poll() is not None:
            raise HardwareFault('GPU 日志监视器退出，停止硬件测试')
        text = self.path.read_text(errors='replace')
        for message in re.findall(r'"eventMessage"\s*:\s*"([^"\n]*)', text):
            if any(word in message.lower() for word in ('gpurestart', 'gpu reset', 'windowserver watchdog')):
                raise HardwareFault('检测到新增 GPU 重置或桌面看门狗异常，停止全部后续测试')

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.output.close()
        self.errors.close()


def wait_for_case(process, timeout, monitor=None):
    deadline = time.monotonic() + timeout
    while True:
        if monitor:
            monitor.check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout)
        try:
            result = process.wait(timeout=min(0.25, remaining))
            if monitor:
                monitor.check()
            return result
        except subprocess.TimeoutExpired:
            continue


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hardware", action="store_true", help="追加默认选择和强制硬件编码测试")
    parser.add_argument("--width", type=int, default=1280, help="偶数宽度，16–4096")
    parser.add_argument("--height", type=int, default=720, help="偶数高度，16–2160")
    parser.add_argument("--frames", type=int, default=3, help="完整编码和回读的帧数，1–120")
    parser.add_argument("--main10", action="store_true", help="HEVC Main10：x420 输入与 10 位回读校验")
    parser.add_argument("--timeout", type=int, default=20, help="每项测试超时秒数，范围 1–60")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 60:
        parser.error("timeout 必须为 1–60 秒")
    if not (16 <= args.width <= 4096 and args.width % 2 == 0
            and 16 <= args.height <= 2160 and args.height % 2 == 0 and 1 <= args.frames <= 120):
        parser.error("尺寸必须在支持范围内且为偶数，帧数必须为 1–120")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    work = ROOT / "build" / "vcn-encode-research" / stamp
    work.mkdir(parents=True, exist_ok=False)
    executable = work / "test-video-encode"
    subprocess.run([
        "xcrun", "clang", "-std=c11", "-fobjc-arc", "-Wall", "-Wextra", "-Werror",
        f"-DNRED_ENCODE_WIDTH={args.width}", f"-DNRED_ENCODE_HEIGHT={args.height}",
        f"-DNRED_ENCODE_FRAMES={args.frames}",
        "-framework", "Foundation", "-framework", "VideoToolbox", "-framework", "CoreMedia",
        "-framework", "CoreVideo", str(ROOT / "tools/test_video_encode.m"), "-o", str(executable),
    ], check=True, timeout=60)
    rows = []
    cases = [("list", ["--list"])]
    modes = ["software"] + (["auto", "hardware"] if args.hardware else [])
    codecs = ["hevc"] if args.main10 else ["h264", "hevc"]
    extra = ["--main10"] if args.main10 else []
    prefix = "main10-" if args.main10 else ""
    cases += [(f"{prefix}{mode}-{codec}", [f"--{mode}", codec, *extra]) for mode in modes for codec in codecs]
    monitor = GPUMonitor(work) if args.hardware else None
    try:
        return run_cases(args, work, executable, cases, monitor, rows)
    finally:
        if monitor: monitor.close()


def run_cases(args, work, executable, cases, monitor, rows):
    for label, options in cases:
        if monitor:
            monitor.check()
        print(f"BEGIN {label}", flush=True)
        with (work / f"{label}.jsonl").open("w") as output, (work / f"{label}.stderr.txt").open("w") as errors:
            process = subprocess.Popen([str(executable), *options], stdout=output, stderr=errors)
            try:
                returncode = wait_for_case(process, args.timeout, monitor)
                row = {"case": label, "returncode": returncode, "timeout": False}
            except HardwareFault as fault:
                process.kill(); process.wait(timeout=10)
                row = {"case": label, "returncode": process.returncode, "timeout": False, "hardwareFault": str(fault)}
            except subprocess.TimeoutExpired:
                # 进程级超时不能解除内核或 GPU 锁；首次阻塞后停止全部后续测试。
                sample_status = None
                try:
                    sample = subprocess.run(["/usr/bin/sample", str(process.pid), "2", "1", "-file",
                                             str(work / f"{label}.sample.txt")],
                                            capture_output=True, text=True, timeout=10)
                    sample_status = sample.returncode
                except (OSError, subprocess.TimeoutExpired):
                    pass
                finally:
                    process.kill()
                    process.wait(timeout=10)
                row = {"case": label, "returncode": process.returncode, "timeout": True,
                       "sampleReturncode": sample_status}
        events = []
        for line in (work / f"{label}.jsonl").read_text().splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                row["invalidJSON"] = True
        row["lastEvent"] = events[-1] if events else None
        rows.append(row)
        (work / "results.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
        summary = dict(row)
        if label == "list" and events:
            summary["lastEvent"] = {"stage": "list", "status": events[-1].get("status"),
                                    "encoderCount": len(events[-1].get("encoders", []))}
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        if row["timeout"] or row.get("hardwareFault"):
            print("STOP: 超时或硬件异常，未继续后续测试；结束进程不代表 GPU 工作已取消", flush=True)
            break
    print("Saved:", work, flush=True)
    return 0 if len(rows) == len(cases) and all(
        r["returncode"] == 0 and not r["timeout"] and not r.get("invalidJSON")
        and not r.get("hardwareFault") for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
