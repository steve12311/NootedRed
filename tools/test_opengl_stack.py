"""用实际 Release 链接映射和机器指令检查 OpenGL / shared-region 入口栈预算。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def inspect(binary, link_map):
    symbols = []
    for line in link_map.read_text().split('# Dead Stripped Symbols:', 1)[0].splitlines():
        match = re.match(r'(0x[0-9A-Fa-f]+)\s+(0x[0-9A-Fa-f]+)\s+\[\s*\d+\]\s+(\S+)', line)
        if match:
            symbols.append((int(match[1], 16), int(match[2], 16), match[3]))
    assembly = subprocess.check_output(['otool', '-tvV', str(binary)], text=True, timeout=60)
    instructions = []
    for line in assembly.splitlines():
        match = re.match(r'([0-9a-f]{16})\s+(.+)', line)
        if match:
            instructions.append((int(match[1], 16), match[2]))
    expected = [
        ('publish', '__ZN12_GLOBAL__N_17publish', 128, 1),
        ('validatePage', '__ZN12_GLOBAL__N_112validatePage', 128, 1),
        ('validateRange', '__ZN12_GLOBAL__N_113validateRange', 128, 1),
        ('patchPage', '__ZN12_GLOBAL__N_19patchPage', 128, 1),
        ('patchBlock', '__ZN12_GLOBAL__N_110patchBlock', 768, 1),
        ('resolvePage', '__ZN18UserOpenGLResolver11resolvePage', 256, 1),
        ('shared-region', '__ZN12_GLOBAL__N_124wrappedSharedRegionCheck', 128, 2),
    ]
    assert not any(symbol.startswith('__ZN12_GLOBAL__N_17prepareEv') for _, _, symbol in symbols), 'unexpected preparation function'
    frames = []
    for name, prefix, budget, count in expected:
        matches = [(address, size, symbol) for address, size, symbol in symbols if symbol.startswith(prefix)]
        assert len(matches) == count, f'{name}: expected {count} functions, found {len(matches)}'
        for address, size, symbol in matches:
            prologue = []
            for pc, text in instructions:
                if not address <= pc < address + size:
                    continue
                if text.startswith('call'):
                    break
                prologue.append(text)
            assert prologue and any(text.startswith('call') for pc, text in instructions
                                    if address <= pc < address + size), f'{name}: missing function body/call'
            reserved = sum(int(match[1], 16) for text in prologue
                           if (match := re.search(r'subq\s+\$(0x[0-9a-f]+), %rsp', text)))
            pushed = sum(text.startswith('pushq') for text in prologue) * 8
            assert reserved + pushed <= budget, f'{name}: {reserved + pushed} bytes exceeds {budget}'
            frames.append(dict(function=name, symbol=symbol, address=hex(address), localReservation=reserved,
                               pushedBytes=pushed, bytesBeforeFirstCall=reserved + pushed, budget=budget,
                               prologue=prologue))
    return dict(binarySHA256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                linkMapSHA256=hashlib.sha256(link_map.read_bytes()).hexdigest(),
                measurement='函数入口至首次调用前的栈预留和 push；不含返回地址、调用者和下层函数', frames=frames)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('link_map', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = inspect(args.binary, args.link_map)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    for frame in report['frames']:
        print(f"PASS {frame['function']}: {frame['bytesBeforeFirstCall']} / {frame['budget']} bytes")


if __name__ == '__main__':
    main()
