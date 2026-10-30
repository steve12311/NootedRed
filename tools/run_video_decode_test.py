"""生成本地短视频，对比软件和强制硬件解码；每个子进程限时 60 秒。"""
from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/vda-research-20261003"


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    executable = WORK / "test-video-decode"
    subprocess.run([
        "xcrun", "clang", "-std=c11", "-fobjc-arc", "-Wall", "-Wextra", "-Wno-unused-parameter",
        "-framework", "Foundation", "-framework", "AVFoundation", "-framework", "VideoToolbox",
        "-framework", "CoreMedia", "-framework", "CoreVideo", str(ROOT / "tools/test_video_decode.m"),
        "-framework", "VideoDecodeAcceleration",
        "-o", str(executable),
    ], check=True, timeout=60)
    rows = []
    for codec, encoder, pix_fmt, options in [
        ("h264", "libx264", "yuv420p", ["-preset", "ultrafast", "-bf", "0"]),
        ("hevc", "libx265", "yuv420p", ["-preset", "ultrafast", "-x265-params",
                              "log-level=error:pools=1:frame-threads=1", "-tag:v", "hvc1"]),
        # HEVC Main10：10 位输入样本，硬件解码须输出 x420 且与软件摘要一致。
        ("hevc10", "libx265", "yuv420p10le", ["-preset", "ultrafast", "-x265-params",
                              "log-level=error:pools=1:frame-threads=1", "-tag:v", "hvc1"]),
    ]:
        sample = WORK / f"{codec}-64x64.mp4"
        # -n 防止覆盖已有样本；不读取用户视频，不联网。
        if not sample.exists():
            subprocess.run([
                "ffmpeg", "-n", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                "testsrc2=size=64x64:rate=3", "-frames:v", "3", "-pix_fmt", pix_fmt,
                "-c:v", encoder, *options, str(sample),
            ], check=True, timeout=60)
        extra = ["--10bit"] if codec == "hevc10" else []
        for mode in (["software", "hardware", "vda"] if codec == "h264" else ["software", "hardware"]):
            try:
                process = subprocess.run([str(executable), f"--{mode}", str(sample), *extra],
                                         capture_output=True, text=True, timeout=60)
                row = {"codec": codec, "mode": mode, "returncode": process.returncode,
                       "stderr": process.stderr}
                try:
                    row["result"] = json.loads(process.stdout)
                except json.JSONDecodeError:
                    row["stdout"] = process.stdout
            except subprocess.TimeoutExpired:
                row = {"codec": codec, "mode": mode, "timeout": True}
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    (WORK / "decode-baseline.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
    for row in rows:
        result = row.get("result", {})
        if row["mode"] == "vda":
            if row.get("returncode") != 0 or result.get("stage") != "vda-create":
                return 1
            continue
        if (row.get("returncode") != 0 or result.get("decodedFrames") != 3
                or result.get("submittedFrames") != 3 or result.get("width") != 64 or result.get("height") != 64
                or result.get("pixelDigest") in (None, "cbf29ce484222325")):
            return 1
        if row["mode"] == "hardware" and result.get("usingHardware") is not True:
            return 1
        if row["mode"] == "software" and result.get("usingHardware") is True:
            return 1
    for codec in ["h264", "hevc", "hevc10"]:
        decoded = {row["mode"]: row["result"] for row in rows if row["codec"] == codec}
        if decoded["software"]["pixelDigest"] != decoded["hardware"]["pixelDigest"]:
            print(f"FAIL: {codec} 硬解像素摘要与软件对照不同", flush=True)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
