"""从参考缓存提取 VideoToolbox 通用筛选入口和 ABI。"""
import bisect
import struct

from audit_video_driver import symbols
from metal_cache import macho

VT = '/System/Library/Frameworks/VideoToolbox.framework/Versions/A/VideoToolbox'

# public call, selector call, list callback, FourCC conversion, native mode test
LAYOUTS = {
    23: (0x23, 0x50C, 0x1DC, 0x334, 0x398),
    24: (0x20, 0x84, 0x1A3, 0x337, 0x37B),
    25: (0x20, 0x7D, 0x1C4, 0x33F, 0x38D),
}
FOURCC = bytes.fromhex('0fb645d1c1e0180fbe4dd2c1e11009c10fbe45d3c1e0080fbe7dd409c709cf')
ADMISSION = bytes.fromhex('8b7dd10fcf837b1402771481ff31307661740881ff393070767504c6431402')


def function_starts(cache, base):
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

    return starts


def prepare(cache, major=25):
    public_call, selector_call, callback, fourcc, mode = LAYOUTS[major]
    lines = (cache.directory / 'dyld_shared_cache_x86_64h.map').read_text().splitlines()
    base = int(lines[lines.index(VT) + 1].split()[1], 16)
    names, _ = symbols(cache.read, base)
    starts = function_starts(cache, base)

    def call(address):
        raw = cache.read(address, 5)
        if raw[0] != 0xE8:
            raise ValueError('VideoToolbox 筛选调用链不匹配')
        return address + 5 + struct.unpack_from('<i', raw, 1)[0]

    selector = call(names['_VTSelectAndCreateVideoDecoderInstance'] + public_call)
    listing = call(selector + selector_call)
    lea = cache.read(listing + callback, 7)
    if lea[:3] != bytes.fromhex('488d35'):
        raise ValueError('VideoToolbox 候选筛选回调不匹配')
    address = listing + callback + 7 + struct.unpack_from('<i', lea, 3)[0]
    index = bisect.bisect_left(starts, address)
    if index + 1 >= len(starts) or starts[index] != address:
        raise ValueError('VideoToolbox 筛选回调不是完整函数入口')
    code = cache.read(address, starts[index + 1] - address)
    if (code[:13] != bytes.fromhex('554889e5415741564155415453')
            or code[mode:mode + 4] != bytes.fromhex('837b1402')
            or code[fourcc + len(FOURCC)] != 0xE8
            or bytes.fromhex('488b3b488d75d1') not in code[fourcc - 40:fourcc]):
        raise ValueError('VideoToolbox 筛选上下文 ABI 不匹配')
    if major == 25 and (code[0x210:0x217] != bytes.fromhex('488b7dc8488d05')
                        or code[0x21B:0x222] != bytes.fromhex('488b30488d55d7')
                        or code[0x48C:0x492] != bytes.fromhex('4531f64531ed')):
        raise ValueError('VideoToolbox Tahoe 筛选上下文 ABI 不匹配')
    if code[fourcc:fourcc + len(FOURCC)] != FOURCC:
        raise ValueError('VideoToolbox FourCC 转换不匹配')
    return address, code


def patched(code, major=25):
    # ASCII FourCC 转换后，将 VP9/AV1 的模式 0/1 交给原生模式 2 准入判断。
    fourcc = LAYOUTS[major][3]
    assert len(FOURCC) == len(ADMISSION)
    assert code[fourcc:fourcc + len(FOURCC)] == FOURCC
    return code[:fourcc] + ADMISSION + code[fourcc + len(FOURCC):]
