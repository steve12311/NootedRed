"""生成 Cezanne scratch 拓扑修复；其他设备保持原生 SE 乘法，不写系统缓存。"""
import hashlib
import re
import struct
import subprocess
from pathlib import Path
from metal_cache import CacheReader, macho

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/user-surface-sync/scratch'
SYMBOL = '__ZL33amdMtl_GFX9_AllocateScratchBufferP28GFX9_MtlComputePipelineStatej'
DIGEST = '901b678273297d0148c2cf9c4812cab5281e80338414eba0f0f3570b4bca8c8d'


def generate(cache):
    WORK.mkdir(parents=True, exist_ok=True)
    address, original = cache.function(SYMBOL)
    if len(original) != 452 or hashlib.sha256(original).hexdigest() != DIGEST:
        raise ValueError('scratch 分配函数未核实')
    source = WORK / 'native.s'
    source.write_text('.text\n.globl _native\n_native:\n.byte ' + ','.join(str(x) for x in original) + '\n')
    obj = WORK / 'native.o'
    subprocess.run(['clang', '-c', str(source), '-o', str(obj)], check=True, timeout=60)
    result = subprocess.run(['xcrun', 'llvm-objdump', '-d', str(obj)], capture_output=True, text=True,
                            check=True, timeout=60)
    instructions = []
    for line in result.stdout.splitlines():
        match = re.match(r'\s*([0-9a-f]+):\s*((?:[0-9a-f]{2}\s+)+)\s*([a-z][a-z0-9]*)\s*(.*)', line)
        if match:
            instructions.append((int(match[1], 16), bytes.fromhex(match[2]), match[3], match[4]))
    if b''.join(item[1] for item in instructions) != original:
        raise ValueError('反汇编不完整')
    cursor = 0
    for offset, data, _, _ in instructions:
        if offset != cursor:
            raise ValueError(f'指令边界不匹配：{offset:#x}')
        cursor += len(data)
    assembly = ['// 自动核实原生函数，保留所有外部调用和 RIP 寻址。', '.text',
                '.globl _compute_scratch_allocate', '_compute_scratch_allocate:']
    def emit(data):
        assembly.append('.byte ' + ','.join(f'0x{x:02x}' for x in data))
    def relative(prefix, target):
        emit(prefix)
        assembly.append(f'.long 0x{target:x} - 0x{address:x} - (. - _compute_scratch_allocate + 4)')
    for offset, data, mnemonic, operand in instructions:
        assembly.append(f'L{offset:x}:')
        if offset in [0x39, 0xBA, 0xC7]:
            continue
        if offset in [0x32, 0xB3]:
            target = address + offset + len(data) + struct.unpack('<i', data[-4:])[0]
            relative(bytes.fromhex('4c8b2d' if offset == 0x32 else '488b35'), target)
        elif offset == 0x49:
            emit(bytes.fromhex('83c9ff'))  # ecx=UINT32_MAX；下一条 cmp 重建 flags。
        elif offset == 0xBD:
            emit(bytes.fromhex('8bbc3138020000'))  # 同一不可变 ivar offset，只读取一次。
        elif mnemonic.startswith('j') or (mnemonic == 'callq' and not operand.startswith('*')):
            match = re.match(r'0x([a-f0-9]+)', operand)
            if not match:
                raise ValueError('未知相对分支')
            target = int(match[1], 16)
            if target >= 1 << 63:
                target -= 1 << 64
            if 0 <= target < len(original):
                assembly.append(f'{mnemonic} L{target:x}')
            elif mnemonic == 'callq':
                relative(b'\xe8', address + target)
            else:
                raise ValueError('未知外部分支')
        elif '%rip' in operand:
            target = address + offset + len(data) + struct.unpack('<i', data[-4:])[0]
            relative(data[:-4], target)
        else:
            emit(data)
        if offset == 0x55:
            # numShaderArrays * numCUPerArray；仅 Cezanne 跳过虚拟的 4 SE。
            emit(bytes.fromhex('43817c2e0c38160000'))
            assembly.append('je L63')
    patched_source = WORK / 'patched.s'
    patched_source.write_text('\n'.join(assembly) + '\n')
    patched_obj = WORK / 'patched.o'
    subprocess.run(['clang', '-c', str(patched_source), '-o', str(patched_obj)], check=True, timeout=60)
    data = patched_obj.read_bytes()
    _, sections, _ = macho(lambda a, n: data[a:a+n], 0)
    section = next(s for s in sections if s[0] == '__text')
    patched = data[section[3]:section[3] + section[2]]
    if len(patched) != len(original) or address // 4096 != (address + len(patched) - 1) // 4096:
        raise ValueError(f'补丁越界：{len(patched)}')
    if patched[0xCA:] != original[0xCA:]:
        raise ValueError('资源分配和释放尾部被改变')
    return {'label': 'compute-scratch-se', 'address': address, 'original': original.hex(), 'patched': patched.hex()}


if __name__ == '__main__':
    cache = CacheReader()
    try:
        patch = generate(cache)
        print('Generated scratch patch:', hex(patch['address']), len(bytes.fromhex(patch['patched'])))
    finally:
        cache.close()
