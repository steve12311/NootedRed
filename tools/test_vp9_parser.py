"""VP9 状态重置与压缩头边界回归。"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/vp9/parser'


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    exe = WORK / 'test'
    subprocess.run(['xcrun', 'clang++', '-std=c++17', '-fsanitize=address,undefined',
                    '-I' + str(ROOT / 'NootedRed'), str(ROOT / 'tools/fixtures/vp9-parser.cpp'),
                    '-o', str(exe)], check=True, timeout=60)
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
