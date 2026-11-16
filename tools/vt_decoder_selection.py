"""从参考缓存提取 VideoToolbox 通用筛选入口和 ABI。"""
import bisect
import struct

from audit_video_driver import symbols
from metal_cache import macho

VT = '/System/Library/Frameworks/VideoToolbox.framework/Versions/A/VideoToolbox'


def prepare(cache):
    lines = (cache.directory / 'dyld_shared_cache_x86_64h.map').read_text().splitlines()
    base = int(lines[lines.index(VT) + 1].split()[1], 16)
    names, _ = symbols(cache.read, base)
    segments, _, _ = macho(cache.read, base)
    link = next(s for s in segments if s[0] == '__LINKEDIT')
    commands = cache.read(base + 32, struct.unpack_from('<I', cache.read(base, 32), 20)[0])
    cursor, starts = 0, None
    while cursor < len(commands):
        cmd, size = struct.unpack_from('<II', commands, cursor)
        if cmd == 0x26:
            off, size = struct.unpack_from('<II', commands, cursor + 8)
            data = cache.read(link[1] + off - link[3], size)
            starts, address, shift, value = [], base, 0, 0
            for byte in data:
                value |= (byte & 127) << shift
                if byte & 128:
                    shift += 7
                    if shift > 63:
                        raise ValueError('VideoToolbox 函数边界编码溢出')
                    continue
                if not value:
                    break
                address += value
                starts.append(address)
                shift = value = 0
            break
        cursor += size
    if not starts:
        raise ValueError('VideoToolbox 缺少完整函数边界')

    def call(address):
        raw = cache.read(address, 5)
        if raw[0] != 0xE8:
            raise ValueError('VideoToolbox 筛选调用链不匹配')
        return address + 5 + struct.unpack_from('<i', raw, 1)[0]

    selector = call(names['_VTSelectAndCreateVideoDecoderInstance'] + 0x20)
    listing = call(selector + 0x7D)
    lea = cache.read(listing + 0x1C4, 7)
    if lea[:3] != bytes.fromhex('488d35'):
        raise ValueError('VideoToolbox 候选筛选回调不匹配')
    address = listing + 0x1CB + struct.unpack_from('<i', lea, 3)[0]
    index = bisect.bisect_left(starts, address)
    if index + 1 >= len(starts) or starts[index] != address:
        raise ValueError('VideoToolbox 筛选回调不是完整函数入口')
    code = cache.read(address, starts[index + 1] - address)
    if (code[:13] != bytes.fromhex('554889e5415741564155415453')
            or code[0x210:0x217] != bytes.fromhex('488b7dc8488d05')
            or code[0x21B:0x222] != bytes.fromhex('488b30488d55d7')
            or code[0x48C:0x492] != bytes.fromhex('4531f64531ed')):
        raise ValueError('VideoToolbox 筛选上下文 ABI 不匹配')
    if code[0x33F:0x35E] != bytes.fromhex(
            '0fb645d1c1e0180fbe4dd2c1e11009c10fbe45d3c1e0080fbe7dd409c709cf'):
        raise ValueError('VideoToolbox FourCC 转换不匹配')
    return address, code


def patched(code):
    # CFStringGetCString 已要求 ASCII；movl + bswap 与原逐字节转换等价。
    # 仅将 VP9/AV1 的查询/创建模式 0/1 规范到原生模式 2，沿用已有的执行环境准入检查。
    # 模式 2、未知模式、其他编码及后续原生筛选逻辑保持原样。
    replacement = bytes.fromhex(
        '8b7dd10fcf837b1402771481ff31307661740881ff393070767504c6431402')
    assert len(replacement) == 0x35E - 0x33F
    result = bytearray(code)
    result[0x33F:0x35E] = replacement
    return bytes(result)
