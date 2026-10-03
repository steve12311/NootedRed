"""只读核对 VCN 接入所需的原生驱动、虚表地址点和用户态显卡识别代码。"""
from pathlib import Path
import hashlib
import json
import struct
import subprocess

from metal_cache import CACHE, CacheReader, macho

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/vcn"


def symbols(read, base):
    segments, sections, table = macho(read, base)
    if table is None:
        raise ValueError("Mach-O 没有符号表")
    link = next(s for s in segments if s[0] == "__LINKEDIT")
    offset, count, strings_offset, strings_size = table
    entries = read(link[1] + offset - link[3], count * 16)
    strings = read(link[1] + strings_offset - link[3], strings_size)
    result = {}
    for i in range(count):
        index, kind, _, _, address = struct.unpack_from("<IBBHQ", entries, i * 16)
        if kind & 0xE != 0xE or not address:
            continue
        end = strings.find(b"\0", index)
        if end < index:
            raise ValueError("符号名未终止")
        result[strings[index:end].decode(errors="replace")] = address
    return result, sections


def disk_image(path):
    data = path.read_bytes()
    if struct.unpack_from("<I", data)[0] != 0xFEEDFACF:
        raise ValueError("需要本机 x86_64 Mach-O 驱动")
    cursor, segments = 32, []
    for _ in range(struct.unpack_from("<I", data, 16)[0]):
        command, length = struct.unpack_from("<II", data, cursor)
        if command == 0x19:
            segments.append(struct.unpack_from("<QQQQ", data, cursor + 24))
        cursor += length

    def read(address, length):
        for start, size, offset, file_size in segments:
            if start <= address and address + length <= start + min(size, file_size):
                return data[offset + address - start:offset + address - start + length]
        raise ValueError(f"读取超出驱动文件：{address:#x}")

    return data, read


def vtable(read, names, classname):
    candidates = [(name, address) for name, address in names.items()
                  if name.startswith("__ZTV") and name.endswith("_" + classname)]
    if len(candidates) != 1:
        raise ValueError(f"虚表不唯一：{classname}")
    name, address = candidates[0]
    end = min(a for a in names.values() if a > address)
    # Itanium 的 offset-to-top 与 RTTI 不属于对象可调用的虚表槽。
    entries = struct.unpack("<" + "Q" * ((end - address - 16) // 8), read(address + 16, end - address - 16))
    return {"symbol": name, "symbolAddress": address, "addressPoint": address + 16,
            "slots": [{"offset": i * 8, "target": a} for i, a in enumerate(entries)]}


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    report = {"drivers": {}}
    for label, classname in [("X5000", "AMDVega10Hardware"), ("X6000", "AMDNavi10Hardware")]:
        path = Path(f"/System/Library/Extensions/AMDRadeon{label}.kext/Contents/MacOS/AMDRadeon{label}")
        data, read = disk_image(path)
        # 内核驱动的虚拟地址与文件偏移分开；直接解析本机未剥离的符号表。
        result = subprocess.run(["nm", "-n", str(path)], check=True, capture_output=True, text=True, timeout=60)
        names = {}
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) == 3:
                try:
                    names[fields[2]] = int(fields[0], 16)
                except ValueError:
                    continue
        hw = vtable(read, names, classname)
        reverse = {address: name for name, address in names.items()}
        for slot in hw["slots"]:
            slot["symbol"] = reverse.get(slot["target"])
            # 匿名 getter 的完整指令保留，避免仅凭方法名判定 ABI 一致。
            following = min((a for a in names.values() if a > slot["target"]), default=slot["target"])
            if slot["target"] and 0 < following - slot["target"] <= 32:
                slot["code"] = read(slot["target"], following - slot["target"]).hex()
        report["drivers"][label] = {"sha256": hashlib.sha256(data).hexdigest(), "hardwareVtable": hw,
                                   "vcnMetaclasses": [n for n in names if "VCN" in n and "gMetaClass" in n]}
    cache = CacheReader()
    try:
        lines = (CACHE / "dyld_shared_cache_x86_64h.map").read_text().splitlines()
        image = "/System/Library/Extensions/AMDRadeonVADriver2.bundle/Contents/MacOS/AMDRadeonVADriver2"
        base = int(lines[lines.index(image) + 1].split()[1], 16)
        names, _ = symbols(cache.read, base)
        key = "__ZN17VAAcceleratorInfo8identifyEjj"
        address = names[key]
        end = min(a for a in names.values() if a > address)
        code = cache.read(address, end - address)
        report["userspace"] = {"cacheUUID": cache.uuid.hex(), "image": image, "symbol": key,
                               "address": address, "code": code.hex(), "length": len(code),
                               "sha256": hashlib.sha256(code).hexdigest(),
                               "hardwareInfoAddress": names["__ZN17VAAcceleratorInfo8m_hwInfoE"]}
    finally:
        cache.close()
    (WORK / "video-driver-audit.json").write_text(json.dumps(report, indent=2) + "\n")
    print("VCN metaclasses:", {k: len(v["vcnMetaclasses"]) for k, v in report["drivers"].items()})
    print("VA identify:", report["userspace"]["sha256"])
    print("Audit saved:", WORK / "video-driver-audit.json")


if __name__ == "__main__":
    main()
