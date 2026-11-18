"""验证 VP9 逐帧比较器的启动、完整输出、退出错误和超时路径。"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

import compare_vp9_decode as compare


CASES = {
    'delayed-writer': (1, 1, 0, 0, 0.1, True),
    'p010': (2, 2, 0, 0, 0, True),
    'probe-fails-before-open': (0, 1, 1, 0, 0, False),
    'missing-reference-frame': (1, 2, 0, 0, 0, False),
    'extra-hardware-frame': (2, 1, 0, 0, 0, False),
    'reference-error': (1, 1, 0, 1, 0, False),
    'timeout-before-open': (0, 1, 0, 0, 10, False),
}


def run_case(name):
    hardware_frames, reference_frames, probe_error, reference_error, delay, expected = CASES[name]
    frame_bytes = 12 if name == 'p010' else 6
    source_format = 'yuv420p10le' if name == 'p010' else 'yuv420p'
    output_format = 'p010le' if name == 'p010' else 'nv12'
    with tempfile.TemporaryDirectory() as work:
        probe = Path(work) / 'probe'
        probe.write_text(f'''#!/usr/bin/env python3
import os, sys, time
time.sleep({delay})
if {probe_error}: sys.exit({probe_error})
with open(os.environ['VP9_DUMP'], 'wb') as out:
    out.write(bytes([1]) * {frame_bytes} * {hardware_frames})
print('VTDecompressionSessionCreate = 0')
print('decoded ok {hardware_frames}, bad 0')
''')
        probe.chmod(0o755)
        compare.PROBE = probe
        popen = subprocess.Popen

        def start(args, **kwargs):
            if args[0] == 'ffmpeg':
                assert args[args.index('-pix_fmt') + 1] == output_format
                args = [sys.executable, '-c',
                        f'import sys;sys.stdout.buffer.write(bytes([1])*{frame_bytes}*{reference_frames});'
                        f'sys.stdout.flush();sys.exit({reference_error})']
            return popen(args, **kwargs)

        with patch.object(compare.subprocess, 'run',
                          return_value=subprocess.CompletedProcess([], 0, '2,2,' + source_format)):
            with patch.object(compare.subprocess, 'Popen', side_effect=start):
                start_time = time.monotonic()
                actual, summary = compare.compare('fixture.ivf', 0.5 if delay > 1 else 2)
        assert actual == expected, (name, actual, expected, summary)
        assert time.monotonic() - start_time < 3, name
        print(json.dumps({'case': name, 'passed': True}))


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--case':
        run_case(sys.argv[2])
        return
    for name in CASES:
        result = subprocess.run([sys.executable, __file__, '--case', name], capture_output=True, text=True,
                                timeout=5)
        assert result.returncode == 0, (name, result.stdout, result.stderr)
        print('PASS', name, flush=True)


if __name__ == '__main__':
    main()
