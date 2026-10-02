"""执行真实 scratch 拓扑指令，验证设备限制、溢出语义和原生尾部保持。"""
import ctypes
import json
import mmap
import struct
from pathlib import Path
from metal_cache import CacheReader
from build_compute_scratch_patch import generate

ROOT = Path(__file__).resolve().parents[1]


def function(code):
    # 保留被测指令使用的 callee-saved 寄存器。
    prefix = bytes.fromhex('415541564989fe4531ed')
    suffix = bytes.fromhex('415e415dc3')
    memory = mmap.mmap(-1, mmap.PAGESIZE, prot=mmap.PROT_READ | mmap.PROT_WRITE)
    address = ctypes.addressof(ctypes.c_char.from_buffer(memory))
    memory.write(prefix + code + suffix)
    libc = ctypes.CDLL(None)
    libc.mprotect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
    assert libc.mprotect(address, mmap.PAGESIZE, mmap.PROT_READ | mmap.PROT_EXEC) == 0
    return memory, ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p)(address)


def main():
    data = json.loads((ROOT / 'build/user-surface-sync/patch-data.json').read_text())
    assert data['compute_scratch_records'][:3] == data['legacy_blend_records']
    patch = data['compute_scratch_records'][3]
    cache = CacheReader()
    try:
        assert generate(cache) == patch, '生成数据不可复现'
        original, changed = bytes.fromhex(patch['original']), bytes.fromhex(patch['patched'])
        assert len(original) == len(changed) == 452
        assert changed[0xCA:] == original[0xCA:], '资源分配、释放或原生调用尾部被改变'
        old_memory, old = function(original[0x55:0x69])
        new_memory, new = function(changed[0x50:0x6F])
        cases = 0
        try:
            for device in [0x1638, 0x1636, 0x15D8, 0x6863, 0, 0xFFFF1638]:
                for engines in [0, 1, 2, 4, 8, 0xFFFFFFFF]:
                    for arrays in [0, 1, 2]:
                        for units in [0, 4, 6, 8, 16, 0xFFFFFFFF]:
                            info = ctypes.create_string_buffer(0xB0)
                            struct.pack_into('<I', info, 12, device)
                            struct.pack_into('<I', info, 0x60, units)
                            struct.pack_into('<I', info, 0x78, engines)
                            struct.pack_into('<I', info, 0x80, arrays)
                            assert old(info) == (engines * arrays * units) & 0xFFFFFFFF
                            factor = 1 if device == 0x1638 else engines
                            assert new(info) == (factor * arrays * units) & 0xFFFFFFFF
                            cases += 1
        finally:
            old_memory.close()
            new_memory.close()
    finally:
        cache.close()
    print(f'PASS {cases} native topology cases: only 0x1638 uses one SE; native tail/legacy payload unchanged')


if __name__ == '__main__':
    main()
