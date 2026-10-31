"""定位 AMD kext 可执行文件；Darwin 25 的 kext 只存在于 KernelCollection 中时从本机 KC 只读提取。

提取结果是独立的 MH_KEXT_BUNDLE：保留原 VA 与段内容，把 x86_64 kernel-cache 链式
fixup 还原为目标 VA，并只携带该 kext 的符号表，供 nm/llvm-objdump/disk_image 使用。
输出写入 build/kc/<KC UUID>/，不修改系统文件。
"""
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/kc"
COLLECTIONS = (
    Path("/System/Library/KernelCollections/BootKernelExtensions.kc"),
    Path("/System/Library/KernelCollections/SystemKernelExtensions.kc"),
    Path("/Library/KernelCollections/AuxiliaryKernelExtensions.kc"),
)

LC_SEGMENT_64, LC_SYMTAB, LC_DYSYMTAB, LC_UUID = 0x19, 0x2, 0xB, 0x1B
LC_CHAINED_FIXUPS, LC_FILESET_ENTRY = 0x80000034, 0x80000035
CHAINED_PTR_X86_64_KERNEL_CACHE = 11


def _commands(data, offset):
    count = struct.unpack_from("<I", data, offset + 16)[0]
    cursor = offset + 32
    for _ in range(count):
        command, length = struct.unpack_from("<II", data, cursor)
        yield command, cursor, length
        cursor += length


def _rebase(collection, entry, buffers):
    """按 kext 自身的 LC_CHAINED_FIXUPS 把段副本中的 kernel-cache 链式指针改写为目标 VA。

    x86_64 kernel-cache 指针：target[0:30] cacheLevel[30:32] diversity[32:48] addrDiv[48]
    key[49:51] next[51:63] isAuth[63]，next 以 1 字节为步长。cacheLevel 0/1 均指向本 KC
    （内核与 kext 在同一 VA 空间，VA 即文件偏移），其它级别或认证指针拒绝。
    """
    fixups = next((struct.unpack_from("<I", collection, cursor + 8)[0]
                   for command, cursor, _ in _commands(collection, entry) if command == LC_CHAINED_FIXUPS), None)
    if fixups is None:
        raise ValueError("KC 条目缺少 LC_CHAINED_FIXUPS")
    starts = fixups + struct.unpack_from("<I", collection, fixups + 4)[0]
    count = struct.unpack_from("<I", collection, starts)[0]
    if count != len(buffers):
        raise ValueError("链式 fixup 段数与条目段数不一致")
    for index in range(count):
        relative = struct.unpack_from("<I", collection, starts + 4 + index * 4)[0]
        if relative == 0:
            continue
        start, buffer = buffers[index]
        if buffer is None:
            raise ValueError("__LINKEDIT 不应含链式指针")
        info = starts + relative
        _, page_size, pointer_format, _, _, page_count = struct.unpack_from("<IHHQIH", collection, info)
        if pointer_format != CHAINED_PTR_X86_64_KERNEL_CACHE:
            raise ValueError(f"未核实的链式指针格式 {pointer_format}")
        for page in range(page_count):
            first = struct.unpack_from("<H", collection, info + 22 + page * 2)[0]
            if first == 0xFFFF:
                continue
            if first & 0x8000:
                raise ValueError("未核实的多起点链式页")
            offset = page * page_size + first
            while True:
                raw = struct.unpack_from("<Q", buffer, offset)[0]
                if raw >> 31 & 0x1 or raw >> 63:
                    raise ValueError(f"未核实的跨级/认证指针 {start + offset:#x}")
                struct.pack_into("<Q", buffer, offset, raw & 0x3FFFFFFF)
                step = raw >> 51 & 0xFFF
                if step == 0:
                    break
                offset += step


def _extract(collection_path, identifier, output):
    collection = collection_path.read_bytes()
    entry = None
    for command, cursor, length in _commands(collection, 0):
        if command == LC_FILESET_ENTRY:
            address, file_offset, name_offset = struct.unpack_from("<QQI", collection, cursor + 8)
            name = collection[cursor + name_offset:cursor + length].split(b"\0")[0].decode()
            if name == identifier:
                entry = file_offset
    if entry is None:
        return False
    segments, symtab, kept = [], None, []
    for command, cursor, length in _commands(collection, entry):
        if command == LC_SEGMENT_64:
            name, address, size, file_offset, file_size = struct.unpack_from("<16sQQQQ", collection, cursor + 8)
            segments.append((name.rstrip(b"\0"), address, size, file_offset, file_size, cursor, length))
        elif command == LC_SYMTAB:
            symtab = struct.unpack_from("<IIII", collection, cursor + 8)
        if command != LC_CHAINED_FIXUPS:
            kept.append((command, cursor, length))
    if symtab is None or not segments:
        raise ValueError(f"{identifier}: KC 条目缺少段或符号表")
    copies = {name: bytearray(collection[file_offset:file_offset + file_size])
              for name, _, _, file_offset, file_size, _, _ in segments if name != b"__LINKEDIT"}
    _rebase(collection, entry, [(address, copies.get(name)) for name, address, _, _, _, _, _ in segments])

    # 新文件：头 + 段原样内容（按 VA 顺序紧排）+ 仅本 kext 的符号/字符串。
    symbol_offset, symbol_count, string_offset, string_size = symtab
    strings, symbols, names = bytearray(b"\0"), bytearray(), {}
    for i in range(symbol_count):
        index, kind, section, description, value = struct.unpack_from("<IBBHQ", collection,
                                                                      symbol_offset + i * 16)
        raw = collection[string_offset + index:collection.index(b"\0", string_offset + index)]
        if raw not in names:
            names[raw] = len(strings)
            strings += raw + b"\0"
        symbols += struct.pack("<IBBHQ", names[raw], kind, section, description, value)

    header_size = 32 + sum(length for _, _, length in kept)
    cursor = (header_size + 0xFFF) & ~0xFFF
    layout = {}
    for name, address, _, file_offset, file_size, _, _ in segments:
        if name == b"__LINKEDIT":
            continue
        if name == b"__TEXT" and file_offset == entry:
            layout[name] = 0
            cursor = max(cursor, (file_size + 0xFFF) & ~0xFFF)
            continue
        layout[name] = cursor
        cursor = (cursor + file_size + 0xFFF) & ~0xFFF
    linkedit = cursor
    body = bytearray(linkedit) + symbols + strings
    for name, address, _, file_offset, file_size, _, _ in segments:
        if name in layout:
            body[layout[name]:layout[name] + file_size] = copies[name]

    commands = bytearray()
    for command, cursor, length in kept:
        raw = bytearray(collection[cursor:cursor + length])
        if command == LC_SEGMENT_64:
            name = bytes(raw[8:24]).rstrip(b"\0")
            old = struct.unpack_from("<Q", raw, 40)[0]
            if name == b"__LINKEDIT":
                size = (len(symbols) + len(strings) + 0xFFF) & ~0xFFF
                struct.pack_into("<QQQ", raw, 32, size, linkedit, len(symbols) + len(strings))
            else:
                new = layout[name]
                struct.pack_into("<Q", raw, 40, new)
                for section in range(struct.unpack_from("<I", raw, 64)[0]):
                    at = 72 + section * 80 + 48
                    value = struct.unpack_from("<I", raw, at)[0]
                    if value:
                        struct.pack_into("<I", raw, at, value - old + new)
                    struct.pack_into("<II", raw, at + 8, 0, 0)
        elif command == LC_SYMTAB:
            struct.pack_into("<IIII", raw, 8, linkedit, symbol_count, linkedit + len(symbols), len(strings))
        elif command == LC_DYSYMTAB:
            # 只保留符号分区计数；KC 内无独立的间接符号/重定位表。
            struct.pack_into("<" + "I" * 12, raw, 32, *([0] * 12))
        commands += raw
    header = bytearray(collection[entry:entry + 32])
    struct.pack_into("<II", header, 16, len(kept), len(commands))
    body[0:32] = header
    body[32:32 + len(commands)] = commands
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    temporary.write_bytes(body)
    temporary.replace(output)
    return True


def _uuid(path):
    data = path.open("rb").read(0x10000)
    for command, cursor, _ in _commands(data, 0):
        if command == LC_UUID:
            return data[cursor + 8:cursor + 24].hex().upper()
    raise ValueError(f"{path}: 缺少 LC_UUID")


def driver(name, root=Path("/")):
    """返回 AMDRadeonX5000/X6000 等 kext 的可执行文件；磁盘缺失时从本机 KC 提取。"""
    path = root / f"System/Library/Extensions/{name}.kext/Contents/MacOS/{name}"
    if path.exists():
        return path
    identifier = f"com.apple.kext.{name}"
    for collection in COLLECTIONS:
        collection = root / collection.relative_to("/")
        if not collection.exists():
            continue
        output = WORK / _uuid(collection) / name
        if output.exists() or _extract(collection, identifier, output):
            return output
    raise FileNotFoundError(f"{name}: 磁盘与 KernelCollection 中均未找到")


if __name__ == "__main__":
    import sys
    for value in sys.argv[1:] or ["AMDRadeonX5000", "AMDRadeonX6000"]:
        print(driver(value))
