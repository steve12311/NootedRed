"""只读采集 VPF 实机基线；不安装 kext、不修改 EFI、不重启、不上传。

输出目录包含命令原文、退出码/超时、NRed IORegistry 属性、内核日志筛选、
显示/功耗状态以及 panic/gpuRestart 文件清单。日志可能包含本机路径，
脚本只写入本地 build/vpf/hardware，不发送到外部。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / 'build/vpf/hardware'


def _run(name: str, argv: list[str], timeout: int) -> dict:
    record = {'name': name, 'argv': argv, 'status': 'ok', 'returncode': 0, 'output': ''}
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        record['returncode'] = result.returncode
        record['output'] = result.stdout + (('\n[stderr]\n' + result.stderr) if result.stderr else '')
        if result.returncode != 0:
            record['status'] = 'nonzero'
    except FileNotFoundError:
        record['status'] = 'missing'
    except subprocess.TimeoutExpired as error:
        record['status'] = 'timeout'
        record['output'] = (error.stdout or '') if isinstance(error.stdout, str) else ''
    return record


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _report_files(directory: Path) -> list[dict]:
    result = []
    if not directory.exists():
        return result
    for path in sorted(list(directory.glob('*.panic')) + list(directory.glob('*.gpuRestart'))):
        try:
            stat = path.stat()
        except OSError:
            continue
        result.append({'path': str(path), 'size': stat.st_size, 'mtime': stat.st_mtime})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--last', default='30m', help='log show 的时间窗口')
    parser.add_argument('--dry-run', action='store_true', help='只打印将执行的只读命令')
    args = parser.parse_args()

    predicate = (
        '(process == "kernel") AND ('
        'eventMessage CONTAINS[c] "NRed" OR eventMessage CONTAINS[c] "GPU restart" OR '
        'eventMessage CONTAINS[c] "fence" OR eventMessage CONTAINS[c] "AMDRadeon")'
    )
    commands = [
        ('sw_vers', ['sw_vers'], 30),
        ('uname', ['uname', '-a'], 30),
        ('sysctl', ['sysctl', 'kern.osversion', 'kern.osproductversion', 'hw.model', 'machdep.cpu.brand_string'], 30),
        ('kextstat', ['kmutil', 'showloaded', '--list-only'], 60),
        # NRed 把诊断属性写在 iGPU 的 IOPCIDevice 上，而不是独立的 NRed 类。
        ('ioreg_nred', ['/bin/sh', '-c', 'ioreg -lw0 -r -c IOPCIDevice | grep -E \'"NRed\' || true'], 60),
        ('ioreg_gpu', ['ioreg', '-lw0', '-r', '-c', 'AMDRadeonX6000'], 60),
        ('pmset', ['pmset', '-g', 'custom'], 30),
        ('displays', ['system_profiler', 'SPDisplaysDataType'], 120),
        ('kernel_log', ['log', 'show', '--style', 'compact', '--last', args.last, '--predicate', predicate], 180),
    ]
    if args.dry_run:
        for name, argv, timeout in commands:
            print(f'{name} (timeout {timeout}s): {" ".join(argv)}')
        print('读取 panic/gpuRestart 清单与 NRed.kext 哈希（只读）')
        return 0

    stamp = time.strftime('%Y%m%dT%H%M%S')
    output = args.output / stamp
    output.mkdir(parents=True, exist_ok=True)
    report = {
        'tool': 'vpf.hardware_baseline',
        'schemaVersion': 1,
        'timestamp': stamp,
        'platform': platform.platform(),
        'hostname': platform.node(),
        'commands': [],
        'kexts': [],
        'diagnosticReports': [],
    }
    for name, argv, timeout in commands:
        record = _run(name, argv, timeout)
        (output / f'{name}.txt').write_text(record['output'])
        record.pop('output')
        report['commands'].append(record)

    for candidate in (
        Path('/Library/Extensions/NootedRed.kext'),
        Path('/System/Library/Extensions/NootedRed.kext'),
    ):
        if candidate.exists():
            binary = candidate / 'Contents/MacOS/NootedRed'
            report['kexts'].append(
                {
                    'path': str(candidate),
                    'binary': str(binary),
                    'sha256': _hash_file(binary) if binary.exists() else None,
                }
            )

    for directory in (
        Path('/Library/Logs/DiagnosticReports'),
        Path.home() / 'Library/Logs/DiagnosticReports',
    ):
        report['diagnosticReports'] += _report_files(directory)

    (output / 'report.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(f'PASS hardware baseline (read-only): {output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
