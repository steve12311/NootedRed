"""从生成头中已核验的原生上下文刷新编码补丁，不依赖已卸载的系统缓存。"""
import hashlib
import re
from pathlib import Path
from metal_cache import array

ROOT = Path(__file__).resolve().parents[1]


def extract(text, name):
    match = re.search(r'inline constexpr UInt8 ' + name + r'\[\] = \{(.*?)\n\};', text, re.S)
    if not match:
        raise ValueError(f'缺少内嵌数据：{name}')
    return bytes(int(x, 16) for x in re.findall(r'0x([0-9A-Fa-f]{2})', match[1]))


def refresh(payload):
    from build_video_decode_patch import HASHES
    key = '__ZN11Vcn2Encoder15setEncodeParamsEPvP15VAVendorTexture'
    if len(payload) != 138:
        raise ValueError('编码补丁长度错误')
    updates = []
    for name in ('UserVideoDecodeData', 'UserSequoiaVideoData'):
        path = ROOT / f'NootedRed/{name}.hpp'
        text = path.read_text()
        context = extract(text, 'ResolverOriginal14')
        native = context[:138]
        if hashlib.sha256(native).hexdigest() != HASHES[key]:
            raise ValueError(f'{name} 原生编码函数未核实')
        if len(context) != 292:
            raise ValueError('编码上下文边界错误')
        index = 9 if name == 'UserSequoiaVideoData' else 11
        old = extract(text, f'Original{index}')
        if old == native:
            offset = 0
        elif old == native[49:86]:
            offset = 49
        else:
            raise ValueError('既有编码补丁不匹配')
        # 原生引用只在后续 AVC 函数中；此补丁无外部 rel32 引用。
        for suffix in ('Original', 'Patched'):
            refs = re.search(r'Resolver' + suffix + r'Refs14\[\] = \{(.*?)\n\};', text, re.S)[1]
            if any(int(x) < 138 for x in re.findall(r'\{(\d+),', refs)):
                raise ValueError('编码补丁含未处理的重定位')
        for label, data in [(f'Original{index}', native), (f'Patched{index}', payload)]:
            text = re.sub(r'inline constexpr UInt8 ' + label + r'\[\] = \{.*?\n\};\n',
                          lambda _: array(label, data), text, count=1, flags=re.S)
        pattern = r'\{0x([0-9A-Fa-f]+)ULL, Original%d, Patched%d, sizeof\(Original%d\)\}' % (index, index, index)
        text, count = re.subn(pattern, lambda m: '{0x%XULL, Original%d, Patched%d, sizeof(Original%d)}' %
                             (int(m[1], 16) - offset, index, index, index), text)
        if count != 1:
            raise ValueError('编码记录不唯一')
        text, count = re.subn(r'\{14, ' + str(offset) + r'\}', '{14, 0}', text)
        if count != 1:
            raise ValueError('编码定位记录不唯一')
        updates.append((path, text))
    for path, text in updates:
        path.write_text(text)
        print('Refreshed', path.name)
