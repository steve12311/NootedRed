"""从生成头和固定夹具构建 DVPF 显示清单；不读取系统缓存或驱动文件。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vpf'))
import generate_manifest
import schema as vpf_schema

import display_schema as schema

ROOT = Path(__file__).resolve().parents[2]
OPENGL_HEADER = ROOT / 'NootedRed/UserOpenGLData.hpp'
OPENGL_FIXTURE = ROOT / 'tools/fixtures/opengl-compat.json'
SUBMIT_GUARD_HEADER = ROOT / 'NootedRed/UserSubmitGuardData.hpp'
LEGACY_HEADER = ROOT / 'NootedRed/UserLegacyData.hpp'
NRed_SOURCE = ROOT / 'NootedRed/NRed.cpp'

OPENGL_DRIVER_NAME = 'AMDRadeonX5000GLDriver'
METAL_DRIVER_PATH = ('/System/Library/Extensions/AMDRadeonX5000MTLDriver.bundle/Contents/MacOS/'
                     'AMDRadeonX5000MTLDriver')
USER_LIMIT = 0x0000800000000000

_BYTE_ARRAY = re.compile(r'inline constexpr UInt8 ([A-Za-z_]\w*)\[\] = \{(.*?)\};', re.S)
_REF_ARRAY = re.compile(
    r'inline constexpr\s+UserCacheResolver::Reference\s+([A-Za-z_]\w*)\[\]\s*=\s*\{(.*?)\};', re.S
)
_SCALAR = re.compile(r'inline constexpr\s+(?:UInt8|UInt32|UInt64)\s+([A-Za-z_]\w*)\s*=\s*([^;]+);')


def _number(token: str) -> int:
    token = token.strip()
    if not re.fullmatch(r'(0x[0-9A-Fa-f]+|\d+)(?:ULL|UL|U|L)?', token):
        raise ValueError(f'无法解析的常量: {token!r}')
    return int(re.sub(r'(?:ULL|UL|U|L)$', '', token), 0)


def byte_arrays(path: Path) -> dict[str, bytes]:
    text = path.read_text()
    result = {}
    for name, body in _BYTE_ARRAY.findall(text):
        values = [int(token, 0) for token in body.replace('\n', ' ').split(',') if token.strip()]
        result[name] = bytes(values)
    return result


def reference_arrays(path: Path) -> dict[str, list[dict]]:
    text = path.read_text()
    result = {}
    for name, body in _REF_ARRAY.findall(text):
        rows = []
        for row in re.findall(r'\{([^{}]*)\}', body):
            values = [_number(field) for field in row.split(',')]
            if len(values) != 4:
                raise ValueError(f'{path.name}: {name} 引用行格式非法')
            rows.append({'offset': values[0], 'image': values[1], 'rva': values[2], 'indirect': values[3]})
        result[name] = rows
    return result


def scalars(path: Path) -> dict[str, int]:
    text = path.read_text()
    return {name: _number(value) for name, value in _SCALAR.findall(text)}


def supported_devices() -> list[int]:
    """与 NRed.cpp 的 APU 白名单保持一致；不引入第二份设备列表。"""
    source = NRed_SOURCE.read_text()
    start = source.index('switch (this->deviceID)')
    end = source.index('default: PANIC', start)
    devices = [int(value, 16) for value in re.findall(r'case (0x[0-9A-Fa-f]+):', source[start:end])]
    if not devices or len(devices) != len(set(devices)) or 0x1638 not in devices:
        raise ValueError('NRed PCI 分类未核实')
    return [0x1638] + sorted(set(devices) - {0x1638})


def _feature(**values) -> dict:
    values.setdefault('sharedFeature', None)
    values.setdefault('requiredGroupMask', 0)
    values.setdefault('conflictGroupMask', 0)
    values.setdefault('pageLocalFallback', False)
    values.setdefault('installable', True)
    values.setdefault('cacheUUID', None)
    values.setdefault('sites', [])
    values.setdefault('sharedSites', [])
    values.setdefault('trampoline', None)
    values.setdefault('refs', {})
    return values


def load_opengl() -> list[dict]:
    fixture = json.loads(OPENGL_FIXTURE.read_text())
    arrays = byte_arrays(OPENGL_HEADER)
    refs = reference_arrays(OPENGL_HEADER)
    result = []
    for profile in fixture['profiles']:
        major = profile['major']
        pages = [bytes.fromhex(page['bytes']) for page in profile['pages']]
        for index, page in enumerate(pages):
            name = f'Page{major}_{index}'
            if arrays.get(name) != page:
                raise ValueError(f'{name} 与 OpenGL 夹具不一致')
        hw, dual = profile['hw'], profile['dual']
        if arrays.get(f'NativeHw{major}') is None or arrays[f'NativeHw{major}'][0x128:] != pages[0][
                hw['tailOffset']:hw['tailOffset'] + 256]:
            raise ValueError(f'Darwin{major} HwInfo 原始尾部不一致')
        if arrays.get(f'NativeDual{major}') != pages[1][dual['offset']:dual['offset'] + 142]:
            raise ValueError(f'Darwin{major} DualQuad 原始函数不一致')
        hw_offset = hw['address'] + 0x128
        dual_offset = dual['address']
        hw_page = hw_offset - hw['tailOffset']
        dual_page = dual_offset - dual['offset']
        if hw_page & 4095 or dual_page & 4095:
            raise ValueError(f'Darwin{major} OpenGL 参考页未对齐')
        for device in supported_devices():
            table = f'OS{major}PCI{device:04X}'
            for kind, feature_name, offset, page, page_offset, size, instruction in (
                (0, 'OpenGLHwInfo', hw_offset, hw_page, hw['tailOffset'], 256, 0x128),
                (1, 'OpenGLDualQuad', dual_offset, dual_page, dual['offset'], 142, 0),
            ):
                replacement = arrays.get(f'{table}{"Hw" if kind == 0 else "Dual"}')
                if replacement is None or len(replacement) != size:
                    raise ValueError(f'{table} 的 {feature_name} 载荷缺失或长度不符')
                original = pages[kind][page_offset:page_offset + size]
                original_refs = refs.get(f'Original{"Hw" if kind == 0 else "Dual"}Refs{major}', [])
                patched_refs = refs.get(f'Patched{"Hw" if kind == 0 else "Dual"}Refs{major}', [])
                result.append(
                    _feature(
                        key=f'{feature_name}_Darwin{major}_{device:04X}',
                        feature=feature_name,
                        domain='OpenGLValidatedPage',
                        group='GLHwInfo' if kind == 0 else 'GLDualQuad',
                        os=[f'Darwin{major}'],
                        osMask=schema.OS_BITS[f'Darwin{major}'],
                        deviceID=device,
                        evidence='ExactKnownPage',
                        maxSites=1,
                        maxPatchedBytes=size,
                        pageLocalFallback=True,
                        installable=True,
                        imagePath=OPENGL_DRIVER_NAME,
                        source='UserOpenGLData.hpp',
                        sites=[
                            {
                                'offset': offset,
                                'size': size,
                                'original': original,
                                'replacement': replacement,
                                'dynamicOffset': 0,
                                'dynamicSize': 0,
                                'functionIndex': kind,
                                'instructionOffset': instruction,
                                'functionRVA': hw['address'] if kind == 0 else dual['address'],
                                'functionSize': 552 if kind == 0 else 142,
                                'pageFileOffset': page,
                                'pageOffset': page_offset,
                                'pageHash': schema.format_digest(pages[kind]),
                                'cppPatch': f'UserOpenGLData::{table}Patches[{kind}]',
                                'cppLocation':
                                    f'UserOpenGLData::Resolver{major}.locations[{kind}].offset',
                            }
                        ],
                        refs={
                            'original': original_refs,
                            'patched': patched_refs,
                        },
                    )
                )
    return result


_METAL = {
    'DisplaySRD': ('MetalSrdShared', 'MetalRenderCore'),
    'DisplayLegacyBlend': ('MetalLegacyBlend', 'MetalRenderCore'),
    'DisplayComputeScratch': ('MetalComputeScratch', 'MetalScratch'),
    'DisplayPipelineConstructor': ('MetalPipelineConstructor', 'MetalRenderCore'),
    'DisplaySubmit': ('MetalSubmit', 'MetalSubmitSafety'),
}


def load_metal() -> list[dict]:
    result = []
    # Darwin 23 的 LegacyBlend/SubmitGuard 仍走 UserLegacyData 精确 profile。
    arrays = byte_arrays(LEGACY_HEADER)
    text = LEGACY_HEADER.read_text()
    patch = re.search(r'OS23MetalPatches\[\] = \{\s*\{0x([0-9A-Fa-f]+)ULL, OS23Gate, OS23GatePatched, (\d+)\},?\s*\};',
                      text)
    page = re.search(r'OS23MetalPages\[\] = \{\{0x([0-9A-Fa-f]+)ULL, OS23MetalPage\}\};', text)
    if not patch or not page:
        raise ValueError('Darwin23 Metal legacy profile 格式未核实')
    if arrays['OS23GatePatched'] != bytes([0xEB, 0x0B]):
        raise ValueError('Darwin23 Metal gate 载荷未核实')
    result.append(
        _feature(
            key='MetalLegacyBlend_Darwin23',
            feature='MetalLegacyBlend',
            domain='MetalSharedCache',
            group='MetalRenderCore',
            os=['Darwin23'],
            osMask=schema.OS_BITS['Darwin23'],
            deviceID=0,
            evidence='ExactStaticPayload',
            sharedFeature='DisplayLegacyBlend',
            maxSites=1,
            maxPatchedBytes=int(patch.group(2)),
            installable=True,
            imagePath=METAL_DRIVER_PATH,
            source='UserLegacyData.hpp',
            cacheUUID=arrays['OS23UUID'].hex().upper(),
            sharedSites=[
                {
                    'offset': int(patch.group(1), 16),
                    'size': int(patch.group(2)),
                    'original': arrays['OS23Gate'],
                    'replacement': arrays['OS23GatePatched'],
                    'functionIndex': 0xFFFFFFFF,
                    'instructionOffset': 0,
                    'dynamicOffset': 0,
                    'dynamicSize': 0,
                    'pageFileOffset': int(page.group(1), 16),
                    'pageOffset': int(patch.group(1), 16) - int(page.group(1), 16),
                    'pageHash': schema.format_digest(arrays['OS23MetalPage']),
                }
            ],
            cppSharedSites='SharedSites_MetalLegacyBlend_Darwin23',
        )
    )
    for feature in generate_manifest.build_all():
        if feature['feature'] not in _METAL:
            continue
        display_feature, group = _METAL[feature['feature']]
        if feature['evidence'] == 'ExactValidatedBuild':
            evidence = 'ExactStaticPayload'
        else:
            evidence = 'FullFunctionWithRefs'
        shared_sites = []
        for patch in feature['patches']:
            shared_sites.append(
                {
                    'offset': patch['referenceOffset'],
                    'size': patch['size'],
                    'original': patch['_original'],
                    'replacement': patch['_patched'],
                    'functionIndex': patch['function'] if patch['function'] is not None else 0xFFFFFFFF,
                    'instructionOffset': patch['instructionOffset'] or 0,
                    'dynamicOffset': 0,
                    'dynamicSize': 0,
                }
            )
        max_sites = {
            'MetalSrdShared': 4,
            'MetalLegacyBlend': 3,
            'MetalComputeScratch': 4,
            'MetalPipelineConstructor': 1,
            'MetalSubmit': 0,
        }[display_feature]
        max_bytes = max((site['size'] for site in shared_sites), default=512 if display_feature ==
                        'MetalComputeScratch' else 0)
        image = feature['images'][0]['path'] if feature['images'] else METAL_DRIVER_PATH
        result.append(
            _feature(
                key=feature['key'],
                feature=display_feature,
                domain='MetalSharedCache',
                group=group,
                os=feature['os'],
                osMask=schema.os_mask(feature['os']),
                deviceID=feature['deviceID'],
                evidence=evidence,
                sharedFeature=feature['feature'],
                maxSites=max_sites,
                maxPatchedBytes=max_bytes,
                pageLocalFallback=False,
                installable=True,
                imagePath=image,
                source=feature['source'],
                cacheUUID=feature['cacheUUID'],
                sharedSites=shared_sites,
            )
        )
    return result


def _legacy_submit_offsets() -> dict[int, int]:
    text = LEGACY_HEADER.read_text()
    start = text.index('inline UInt64 submitOffset(UInt32 major)')
    brace = text.index('{', start)
    depth = 0
    end = brace
    for index in range(brace, len(text)):
        if text[index] == '{':
            depth += 1
        elif text[index] == '}':
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    body = text[brace:end]
    result = {int(major): int(value, 16) for major, value in
              re.findall(r'if \(major == (\d+)\) \{ return (0x[0-9A-Fa-f]+)ULL; \}', body)}
    return result


def load_submit_guard() -> list[dict]:
    arrays = byte_arrays(SUBMIT_GUARD_HEADER)
    values = scalars(SUBMIT_GUARD_HEADER)
    original = arrays['MethodOriginal']
    payload = arrays['Payload']
    patch_size = values['MethodPatchSize']
    payload_jump = values['PayloadJumpOffset']
    if len(original) != patch_size or len(payload) < payload_jump + 8:
        raise ValueError('SubmitGuard 原始入口或载荷长度不符')
    template = bytes([0xFF, 0x25, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x90])
    if len(template) != patch_size:
        raise ValueError('SubmitGuard 入口模板长度不符')
    offsets = _legacy_submit_offsets()
    offsets[25] = values['MethodOffset']
    result = []
    for major in (23, 24, 25):
        offset = offsets.get(major)
        if offset is None:
            raise ValueError(f'缺少 Darwin{major} SubmitGuard 入口偏移')
        result.append(
            _feature(
                key=f'MetalSubmitGuard_Darwin{major}',
                feature='MetalSubmitGuard',
                domain='MetalSharedCache',
                group='MetalSubmitSafety',
                os=[f'Darwin{major}'],
                osMask=schema.OS_BITS[f'Darwin{major}'],
                deviceID=0,
                evidence='RuntimeTrampoline',
                sharedFeature='DisplaySubmitGuard',
                maxSites=1,
                maxPatchedBytes=patch_size,
                installable=True,
                imagePath=METAL_DRIVER_PATH,
                source='UserSubmitGuardData.hpp',
                sites=[
                    {
                        'offset': offset,
                        'size': patch_size,
                        'original': original,
                        'replacement': template,
                        'dynamicOffset': 6,
                        'dynamicSize': 8,
                        'functionIndex': 0xFFFFFFFF,
                        'instructionOffset': 0,
                    }
                ],
                trampoline={
                    'bytes': payload,
                    'size': len(payload),
                    'dynamicOffset': payload_jump,
                    'dynamicSize': 8,
                },
            )
        )
    return result


def load_kernel() -> list[dict]:
    common = {
        'os': ['Darwin23', 'Darwin24', 'Darwin25'],
        'osMask': schema.os_mask(['Darwin23', 'Darwin24', 'Darwin25']),
        'deviceID': 0,
        'maxSites': 0,
        'maxPatchedBytes': 0,
        'installable': False,
    }
    return [
        _feature(
            key='KernelGFX9Capabilities',
            feature='KernelGFX9Capabilities',
            domain='KernelGraphics',
            group='KernelGFX9',
            evidence='KernelSymbolAndABI',
            imagePath='AMDRadeonX5000',
            source='X5000.cpp',
            **common,
        ),
        _feature(
            key='KernelDCNScanout',
            feature='KernelDCNScanout',
            domain='KernelFramebuffer',
            group='KernelScanout',
            evidence='KernelSymbolAndABI',
            imagePath='AMDRadeonX6000Framebuffer',
            source='X6000FB.cpp',
            **common,
        ),
        _feature(
            key='KernelFramebuffer',
            feature='KernelFramebuffer',
            domain='KernelFramebuffer',
            group='KernelScanout',
            evidence='KernelSymbolAndABI',
            imagePath='AMDRadeonX5000',
            source='AMDGFX9DCNDisplay.cpp',
            **common,
        ),
    ]


def validate(features: list[dict]) -> None:
    keys = set()
    for feature in features:
        key = (feature['feature'], tuple(feature['os']), feature['deviceID'])
        if key in keys:
            raise ValueError(f'显示功能重复: {key}')
        keys.add(key)
        allowed = {
            'feature': schema.FEATURES,
            'domain': schema.DOMAINS,
            'group': schema.GROUPS,
            'evidence': schema.EVIDENCE,
        }
        for name, values in allowed.items():
            if feature[name] not in values:
                raise ValueError(f'{feature["key"]}: 未知 {name} {feature[name]}')
        if feature['feature'] not in schema.GROUP_MEMBERS[feature['group']]:
            raise ValueError(f'{feature["key"]}: 功能不属于组 {feature["group"]}')
        if feature['osMask'] != schema.os_mask(feature['os']):
            raise ValueError(f'{feature["key"]}: Darwin 掩码不一致')
        if not 0 <= feature['deviceID'] <= 0xFFFF:
            raise ValueError(f'{feature["key"]}: 设备 ID 非法')
        if not feature['os'] or not feature['imagePath'] or not feature['source']:
            raise ValueError(f'{feature["key"]}: Darwin/映像/来源声明不完整')
        group_bits = (1 << len(schema.GROUPS)) - 1
        if feature['requiredGroupMask'] & ~group_bits or feature['conflictGroupMask'] & ~group_bits:
            raise ValueError(f'{feature["key"]}: 组掩码越界')
        if len(feature['sharedSites']) > feature['maxSites'] or len(feature['sites']) > feature['maxSites']:
            raise ValueError(f'{feature["key"]}: 站点预算超限')
        if feature['domain'] == 'OpenGLValidatedPage':
            if feature['deviceID'] == 0 or len(feature['sites']) != 1 or not feature['pageLocalFallback']:
                raise ValueError(f'{feature["key"]}: OpenGL 设备/页内声明不完整')
        if feature['domain'] == 'MetalSharedCache':
            if feature['sharedFeature'] is None:
                raise ValueError(f'{feature["key"]}: Metal 缺少共享功能绑定')
            if not feature['sharedSites'] and feature['feature'] not in (
                'MetalComputeScratch', 'MetalSubmit', 'MetalSubmitGuard'
            ):
                raise ValueError(f'{feature["key"]}: Metal 缺少静态站点')
        if feature['domain'] in ('KernelGraphics', 'KernelFramebuffer', 'GPUObservation'):
            if feature['sites'] or feature['sharedSites'] or feature['installable']:
                raise ValueError(f'{feature["key"]}: 内核/观测条目不得携带用户态站点或标为可安装')
        for site in feature['sites'] + feature['sharedSites']:
            if not 0 < site['size'] <= feature['maxPatchedBytes']:
                raise ValueError(f'{feature["key"]}: 站点长度越界')
            if len(site['original']) != site['size'] or len(site['replacement']) != site['size']:
                raise ValueError(f'{feature["key"]}: 原始/替换字节长度不符')
            if site['dynamicOffset'] < 0 or site['dynamicSize'] < 0 or site['dynamicOffset'] + site[
                    'dynamicSize'] > site['size']:
                raise ValueError(f'{feature["key"]}: 动态范围越界')
            if site['offset'] < 0 or site['offset'] >= USER_LIMIT or site['size'] > USER_LIMIT - site['offset']:
                raise ValueError(f'{feature["key"]}: 站点地址越界')
        for site in feature['sites']:
            if feature['domain'] == 'OpenGLValidatedPage':
                if (site['offset'] & 4095) + site['size'] > 4096:
                    raise ValueError(f'{feature["key"]}: 页内站点跨页')
                if site['functionIndex'] not in (0, 1):
                    raise ValueError(f'{feature["key"]}: OpenGL 函数索引非法')
        if feature['trampoline'] is not None:
            trampoline = feature['trampoline']
            if len(trampoline['bytes']) != trampoline['size'] or trampoline['dynamicOffset'] + trampoline[
                    'dynamicSize'] > trampoline['size']:
                raise ValueError(f'{feature["key"]}: 跳板载荷长度不符')
    features.sort(key=lambda item: (schema.FEATURE_ORDER[item['feature']], item['osMask'], item['deviceID']))
    if len(features) != len(keys):
        raise ValueError('显示功能键重复')


def build_all() -> list[dict]:
    features = load_opengl() + load_metal() + load_submit_guard() + load_kernel()
    validate(features)
    # 排序后再次校验唯一性；validate 已保证无重复。
    features.sort(key=lambda item: (schema.FEATURE_ORDER[item['feature']], item['osMask'], item['deviceID']))
    return features


def strip(feature: dict) -> dict:
    def clean(value):
        if isinstance(value, bytes):
            return value.hex()
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()
                if not key.startswith('_') and not key.startswith('cpp')}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return clean(feature)
