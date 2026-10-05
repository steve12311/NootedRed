"""生成完整函数及外部 rel32 引用，供运行时重新定位与核验。"""
import re
import struct
import subprocess
from metal_cache import CACHE, array, macho


class ResolverData:
    def __init__(self, cache, work):
        self.cache, self.work = cache, work
        self.catalog, self.images, self.functions = [], [], []
        path, ranges = None, []
        for line in (CACHE / 'dyld_shared_cache_x86_64h.map').read_text().splitlines():
            if line.startswith('/'):
                if path:
                    self.catalog.append((path, ranges))
                path, ranges = line, []
            elif path and (m := re.match(r'\s+(__\w+) (0x[\da-fA-F]+) -> (0x[\da-fA-F]+)', line)):
                ranges.append((m[1], int(m[2], 16), int(m[3], 16)))
        if path:
            self.catalog.append((path, ranges))

    def owner(self, address):
        matches = [(path, ranges) for path, ranges in self.catalog
                   if any(name != '__LINKEDIT' and start <= address < end for name, start, end in ranges)]
        if len(matches) != 1:
            raise ValueError(f'引用目标映像不唯一：{address:#x}')
        path, ranges = matches[0]
        base = next(start for name, start, _ in ranges if name == '__TEXT')
        if path not in [p for p, _ in self.images]:
            self.images.append((path, base))
        return next(i for i, (p, _) in enumerate(self.images) if p == path), base

    def pointer(self, address):
        for start, end, _, handle in self.cache.mappings:
            if not start <= address < end:
                continue
            handle.seek(312)
            offset, count = struct.unpack('<II', handle.read(8))
            handle.seek(offset)
            records = handle.read(count*56)
            for i in range(count):
                base, size, _, slide, slide_size, *_ = struct.unpack_from('<QQQQQQII', records, i*56)
                if not base <= address < base+size or slide_size < 40:
                    continue
                handle.seek(slide)
                info = handle.read(40)
                if struct.unpack_from('<I', info)[0] != 2:
                    raise ValueError('参考缓存需 slide_info2')
                mask, add = struct.unpack_from('<QQ', info, 24)
                raw = struct.unpack('<Q', self.cache.read(address, 8))[0] & ~mask
                if raw == 0:
                    raise ValueError('空 GOT 引用')
                return raw+add
        raise ValueError('GOT 无有效重定位映射')

    def references(self, code, address, label):
        # LC_DATA_IN_CODE 标记的跳转表不能当作 x86 指令分析；其字节仍参与完整匹配。
        _, image = self.owner(address)
        segments, _, _ = macho(self.cache.read, image)
        link = next(s for s in segments if s[0] == '__LINKEDIT')
        commands = self.cache.read(image+32, struct.unpack_from('<I', self.cache.read(image,32),20)[0])
        cursor = 0
        decoded_input = bytearray(code)
        while cursor < len(commands):
            kind, length = struct.unpack_from('<II',commands,cursor)
            if kind == 0x29:
                offset, size = struct.unpack_from('<II',commands,cursor+8)
                entries = self.cache.read(link[1]+offset-link[3],size)
                for off, count, _ in struct.iter_unpack('<IHH',entries):
                    start = image+off
                    if not any(s[1] <= start and start+count <= s[1]+s[2] for s in segments):
                        raise ValueError('代码内数据未映射')
                    first, last = max(start,address), min(start+count,address+len(code))
                    if first < last:
                        decoded_input[first-address:last-address] = b'\x90'*(last-first)
            cursor += length
        source = self.work / f'resolver-{label}.s'
        source.write_text('.text\n.globl _native\n_native:\n.byte ' + ','.join(map(str, decoded_input)) + '\n')
        obj = source.with_suffix('.o')
        subprocess.run(['xcrun', 'clang', '-c', str(source), '-o', str(obj)], check=True, timeout=60)
        output = subprocess.run(['xcrun', 'llvm-objdump', '-d', str(obj)], check=True,
                                capture_output=True, text=True, timeout=60).stdout
        decoded, fields = bytearray(), []
        for line in output.splitlines():
            m = re.match(r'\s*([0-9a-f]+):\s*((?:[0-9a-f]{2}\s+)+)\s*(\S+)\s*(.*)', line)
            if not m:
                continue
            offset, raw, mnemonic, operand = int(m[1], 16), bytes.fromhex(m[2]), m[3], m[4]
            if offset != len(decoded):
                raise ValueError(f'重定位反汇编不连续：{label} {offset:#x} expected {len(decoded):#x} line={line}')
            decoded.extend(raw)
            direct = ((raw[0] in (0xE8, 0xE9) and len(raw) == 5)
                      or (len(raw) == 6 and raw[0] == 0x0F and 0x80 <= raw[1] <= 0x8F))
            if '%rip' not in operand and not direct:
                continue
            displacement_offset = len(raw)-4
            if '%rip' in operand:
                rip = re.search(r'(-?(?:0x[0-9a-f]+|[0-9]+))\(%rip\)', operand)
                if not rip:
                    raise ValueError('RIP 位移未解码')
                displacement = int(rip[1], 0)
                encoded = struct.pack('<i', displacement)
                if raw.count(encoded) != 1:
                    raise ValueError('RIP 位移字段不唯一')
                displacement_offset = raw.index(encoded)
                # 位移后有立即数时 rel32 基准仍在指令末尾；本解析器只接受末尾字段。
                if displacement_offset+4 != len(raw):
                    raise ValueError(f'不支持带尾部立即数的 RIP 引用：{line}')
            target = address + offset + len(raw) + struct.unpack_from('<i', raw, displacement_offset)[0]
            if address <= target < address + len(code):
                continue
            indirect = 0
            try:
                image, base = self.owner(target)
            except ValueError:
                # 缓存合并 GOT / branch island 没有 Mach-O owner；核验其实际绑定目标。
                indirect = 1
                if direct:
                    stub = self.cache.read(target, 6)
                    if stub[:2] != b'\xff\x25':
                        raise ValueError('未核实的共享缓存调用桩')
                    indirect = 2
                    target = target+6+struct.unpack_from('<i', stub, 2)[0]
                target = self.pointer(target)
                image, base = self.owner(target)
            fields.append((offset + displacement_offset, image, target - base, indirect))
        if bytes(decoded) != bytes(decoded_input) or len(fields) > 1024:
            raise ValueError(f'函数反汇编或引用预算不完整：{label} bytes={len(decoded)}/{len(code)} refs={len(fields)}')
        return fields

    def add(self, address, original, patched):
        image, base = self.owner(address)
        if len(original) != len(patched) or not 32 <= len(original) <= 65536:
            raise ValueError('完整函数长度不合法')
        i = len(self.functions)
        self.functions.append((image, address - base, original,
                               self.references(original, address, f'{i}-original'),
                               self.references(patched, address, f'{i}-patched')))
        return i

    def header(self, locations):
        out = 'inline constexpr UserCacheResolver::ImageReference ResolverImages[] = {\n'
        out += ''.join(f'    {{"{p}", sizeof("{p}")}},\n' for p, _ in self.images) + '};\n'
        for i, (_, _, original, original_refs, patched_refs) in enumerate(self.functions):
            out += array(f'ResolverOriginal{i}', original)
            for name, fields in [('Original', original_refs), ('Patched', patched_refs)]:
                if fields:
                    out += f'inline constexpr UserCacheResolver::Reference Resolver{name}Refs{i}[] = {{\n'
                    out += ''.join(f'    {{{offset}, {image}, 0x{rva:X}ULL, {indirect}}},\n' for offset, image, rva, indirect in fields) + '};\n'
        out += 'inline constexpr UserCacheResolver::Function ResolverFunctions[] = {\n'
        for i, (image, rva, original, orig, patch) in enumerate(self.functions):
            o = f'ResolverOriginalRefs{i}' if orig else 'nullptr'
            p = f'ResolverPatchedRefs{i}' if patch else 'nullptr'
            out += f'    {{{image}, 0x{rva:X}ULL, ResolverOriginal{i}, sizeof(ResolverOriginal{i}), {o}, {len(orig)}, {p}, {len(patch)}}},\n'
        out += '};\ninline constexpr UserCacheResolver::Location ResolverLocations[] = {\n'
        out += ''.join(f'    {{{function}, {offset}}},\n' for function, offset in locations) + '};\n'
        if len(self.images) > 16 or len(self.functions) > 32:
            raise ValueError('映像或函数预算超限')
        return out


def containing_function(cache, image, address):
    # 内部函数采用 LC_FUNCTION_STARTS，而非剥离符号之间的大区间。
    segments, _, _ = macho(cache.read, image)
    link = next(s for s in segments if s[0] == '__LINKEDIT')
    commands = cache.read(image + 32, struct.unpack_from('<I', cache.read(image, 32), 20)[0])
    cursor, starts = 0, []
    while cursor < len(commands):
        kind, length = struct.unpack_from('<II', commands, cursor)
        if kind == 0x26:
            offset, size = struct.unpack_from('<II', commands, cursor + 8)
            data = cache.read(link[1] + offset - link[3], size)
            value, shift, current = 0, 0, image
            for byte in data:
                value |= (byte & 127) << shift
                if byte & 128:
                    shift += 7
                    continue
                if not value:
                    break
                current += value
                starts.append(current)
                value = shift = 0
        cursor += length
    start = max(a for a in starts if a <= address)
    end = min(a for a in starts if a > start)
    return start, cache.read(start, end - start)
