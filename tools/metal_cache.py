"""读取指定版本的 AMD Metal dyld 缓存，为补丁生成和回归验证提供只读数据。"""
from pathlib import Path
import struct

CACHE = Path("/System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld")
IMAGE = "/System/Library/Extensions/AMDRadeonX5000MTLDriver.bundle/Contents/MacOS/AMDRadeonX5000MTLDriver"


def macho(read, base):
    header = read(base, 32)
    if struct.unpack_from("<I", header)[0] != 0xFEEDFACF:
        raise ValueError("不是 64 位 Mach-O")
    commands = read(base + 32, struct.unpack_from("<I", header, 20)[0])
    segments, sections, symbols = [], [], None
    cursor = 0
    for _ in range(struct.unpack_from("<I", header, 16)[0]):
        command, length = struct.unpack_from("<II", commands, cursor)
        if command == 0x19:
            name, address, size, offset, file_size, _, _, count, _ = struct.unpack_from(
                "<16sQQQQIIII", commands, cursor + 8)
            segments.append((name.rstrip(b"\0").decode(), address, size, offset, file_size))
            for i in range(count):
                name, _, address, size, offset, _, _, _, _, _, _, _ = struct.unpack_from(
                    "<16s16sQQIIIIIIII", commands, cursor + 72 + i * 80)
                sections.append((name.rstrip(b"\0").decode(), address, size, offset))
        if command == 2:
            symbols = struct.unpack_from("<IIII", commands, cursor + 8)
        cursor += length
    return segments, sections, symbols


class CacheReader:
    def __init__(self):
        self.files, self.mappings = [], []
        self.base = 0
        try:
            for path in sorted(CACHE.glob("dyld_shared_cache_x86_64h*")):
                if path.suffix in (".map", ".atlas"):
                    continue
                handle = path.open("rb")
                self.files.append(handle)
                header = handle.read(104)
                if not header.startswith(b"dyld_v1"):
                    continue
                offset, count = struct.unpack_from("<II", header, 16)
                handle.seek(offset)
                data = handle.read(count * 32)
                for i in range(count):
                    address, size, file_offset, _, _ = struct.unpack_from("<QQQII", data, i * 32)
                    self.mappings.append((address, address + size, file_offset, handle))
                if path.name == "dyld_shared_cache_x86_64h":
                    self.magic, self.uuid = header[:16], header[88:104]
                    self.base = struct.unpack_from("<Q", data)[0]
            if self.base != 0x7FF800000000 or self.uuid.hex() != "3137475689b434e8a34f56c5d57216a1":
                raise ValueError("缓存 UUID/base 未核实，拒绝生成")
            lines = (CACHE / "dyld_shared_cache_x86_64h.map").read_text().splitlines()
            self.image_base = int(lines[lines.index(IMAGE) + 1].split()[1], 16)
            segments, self.sections, table = macho(self.read, self.image_base)
            link = next(s for s in segments if s[0] == "__LINKEDIT")
            offset, count, strings_offset, strings_size = table
            data = self.read(link[1] + offset - link[3], count * 16)
            strings = self.read(link[1] + strings_offset - link[3], strings_size)
            self.symbols = []
            for i in range(count):
                index, kind, _, _, address = struct.unpack_from("<IBBHQ", data, i * 16)
                name = strings[index:strings.find(b"\0", index)].decode(errors="replace")
                if kind & 0xE == 0xE and address:
                    self.symbols.append((address, name))
        except Exception:
            self.close()
            raise

    def read(self, address, length):
        for start, end, offset, handle in self.mappings:
            if start <= address and address + length <= end:
                handle.seek(offset + address - start)
                data = handle.read(length)
                if len(data) != length:
                    raise ValueError("缓存读取不完整")
                return data
        raise ValueError(f"缓存地址未映射：{address:#x}")

    def function(self, name):
        address = next(a for a, n in self.symbols if n == name)
        end = min(a for a, _ in self.symbols if a > address)
        return address, self.read(address, end - address)

    def close(self):
        for handle in self.files:
            handle.close()


def array(name, data):
    rows = [", ".join(f"0x{v:02X}" for v in data[i:i + 16]) for i in range(0, len(data), 16)]
    return f"inline constexpr UInt8 {name}[] = {{\n" + ",\n".join("    " + r for r in rows) + "\n};\n"
