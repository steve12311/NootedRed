"""从现有生成头确定性提取只读 VPF Manifest，并生成 C++ 描述头。

不重新生成补丁数据，也不访问系统缓存；输入是仓库内已审查的生成头，
因此 Manifest 与运行时实际使用的数组逐项对应。生成结果必须提交，
`--check` 用于 CI/测试确认没有陈旧或手改。
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cppdata
import schema

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'NootedRed/VPF/PatchManifestData.hpp'

HEADERS = (
    'UserVideoDecodeData.hpp',
    'UserSequoiaVideoData.hpp',
    'UserSurfaceSyncData.hpp',
    'UserSurfaceConstructorData.hpp',
    'UserSequoiaSurfaceData.hpp',
)

MAX_IMAGES = 16
MAX_FUNCTIONS = 32
MAX_SITES = 32
MAX_PATCH_SIZE = 512


@dataclass(frozen=True)
class FeatureSpec:
    key: str
    feature: str
    os: tuple[str, ...]
    device_id: int
    evidence: str
    header: str
    namespace: str
    patches: str | None
    functions: str | None
    locations: str | None
    uuid_namespace: str | None = None
    uuid_name: str | None = None


def _ident(value, context: str) -> str:
    if not isinstance(value, cppdata.Ident):
        raise ValueError(f'{context}: 期望数组名，得到 {value!r}')
    return value.name


def _size(value, header: cppdata.HeaderData, namespace: str, context: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, cppdata.SizeOf):
        return len(header.byte_array(namespace, value.name))
    raise ValueError(f'{context}: 无法求值的长度 {value!r}')


def _parse_images(header: cppdata.HeaderData, namespace: str) -> list[dict]:
    array = header.maybe_array(namespace, 'ResolverImages')
    if array is None:
        return []
    images = []
    for index, item in enumerate(array.items):
        if not isinstance(item, list) or len(item) != 2 or not isinstance(item[0], str) or not isinstance(item[1], int):
            raise ValueError(f'{header.path.name}: ResolverImages[{index}] 格式非法')
        path, size = item
        if size != len(path) + 1:
            raise ValueError(f'{header.path.name}: ResolverImages[{index}] 长度不是 sizeof(path)')
        images.append({'path': path, 'size': size})
    return images


def _parse_refs(header: cppdata.HeaderData, namespace: str, name, count, context: str) -> list[dict]:
    if name is None:
        if count != 0:
            raise ValueError(f'{context}: 引用数组为空但计数非零')
        return []
    array = header.array(namespace, _ident(name, context))
    if array.count != count:
        raise ValueError(f'{context}: 引用计数 {count} 与数组长度 {array.count} 不一致')
    refs = []
    for index, item in enumerate(array.items):
        if not isinstance(item, list) or len(item) != 4 or not all(isinstance(value, int) for value in item):
            raise ValueError(f'{context}: Reference[{index}] 格式非法')
        offset, image, rva, indirect = item
        refs.append({'offset': offset, 'image': image, 'rva': rva, 'indirect': indirect})
    return refs


def _parse_functions(header: cppdata.HeaderData, namespace: str, images: list[dict]) -> list[dict]:
    array = header.maybe_array(namespace, 'ResolverFunctions')
    if array is None:
        return []
    functions = []
    for index, item in enumerate(array.items):
        if not isinstance(item, list) or len(item) != 8:
            raise ValueError(f'{header.path.name}: ResolverFunctions[{index}] 格式非法')
        image, rva, original_name, size_value, original_refs, original_count, patched_refs, patched_count = item
        if not isinstance(image, int) or not isinstance(rva, int):
            raise ValueError(f'{header.path.name}: ResolverFunctions[{index}] 映像或 RVA 非法')
        original = header.byte_array(namespace, _ident(original_name, 'ResolverFunctions'))
        size = _size(size_value, header, namespace, 'ResolverFunctions')
        if size != len(original):
            raise ValueError(f'{header.path.name}: ResolverFunctions[{index}] 长度不一致')
        functions.append(
            {
                'image': image,
                'rva': rva,
                'size': size,
                '_original': original,
                'originalSha256': hashlib.sha256(original).hexdigest(),
                'originalRefs': _parse_refs(header, namespace, original_refs, original_count, 'originalRefs'),
                'patchedRefs': _parse_refs(header, namespace, patched_refs, patched_count, 'patchedRefs'),
            }
        )
    return functions


def _parse_locations(header: cppdata.HeaderData, namespace: str) -> list[dict]:
    array = header.maybe_array(namespace, 'ResolverLocations')
    if array is None:
        return []
    locations = []
    for index, item in enumerate(array.items):
        if not isinstance(item, list) or len(item) != 2 or not all(isinstance(value, int) for value in item):
            raise ValueError(f'{header.path.name}: ResolverLocations[{index}] 格式非法')
        locations.append({'function': item[0], 'offset': item[1]})
    return locations


def _patch_offset(value, base_patches: list[dict] | None, context: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, cppdata.Ident):
        match = re.fullmatch(r'Patches\[(\d+)\]\.offset', value.name)
        if match and base_patches is not None:
            index = int(match.group(1))
            if index < len(base_patches):
                return base_patches[index]['referenceOffset']
    raise ValueError(f'{context}: 无法解析补丁偏移 {value!r}')


def _parse_patches(header: cppdata.HeaderData, namespace: str, name: str,
                   base_patches: list[dict] | None) -> list[dict]:
    array = header.array(namespace, name)
    patches = []
    for index, item in enumerate(array.items):
        # 设备表可能整项复用基础表：{Patches[1]}
        if isinstance(item, cppdata.Ident):
            match = re.fullmatch(r'Patches\[(\d+)\]', item.name)
            if match and base_patches is not None and int(match.group(1)) < len(base_patches):
                patches.append(dict(base_patches[int(match.group(1))]))
                continue
            raise ValueError(f'{header.path.name}: {name}[{index}] 无法复用基础补丁 {item.name}')
        if not isinstance(item, list) or len(item) != 4:
            raise ValueError(f'{header.path.name}: {name}[{index}] 格式非法')
        offset_value, original_name, patched_name, size_value = item
        original = header.byte_array(namespace, _ident(original_name, name))
        patched = header.byte_array(namespace, _ident(patched_name, name))
        size = _size(size_value, header, namespace, name)
        if size != len(original) or size != len(patched):
            raise ValueError(f'{header.path.name}: {name}[{index}] 原始/替换长度不一致')
        patches.append(
            {
                'referenceOffset': _patch_offset(offset_value, base_patches, name),
                'size': size,
                '_original': original,
                '_patched': patched,
                'originalSha256': hashlib.sha256(original).hexdigest(),
                'patchedSha256': hashlib.sha256(patched).hexdigest(),
            }
        )
    return patches


def _device_patches(header: cppdata.HeaderData, namespace: str) -> list[tuple[int, str]]:
    array = header.maybe_array(namespace, 'DevicePatches')
    if array is None:
        return []
    result = []
    for index, item in enumerate(array.items):
        if not isinstance(item, list) or len(item) < 2 or not isinstance(item[0], int):
            raise ValueError(f'{header.path.name}: DevicePatches[{index}] 格式非法')
        result.append((item[0], _ident(item[1], 'DevicePatches')))
    return result


def _uuid(header: cppdata.HeaderData, namespace: str | None, name: str | None) -> str | None:
    if not namespace or not name:
        return None
    array = header.maybe_array(namespace, name)
    return array.items.hex().upper() if array is not None and isinstance(array.items, bytes) else None


def _specs(headers: dict[str, cppdata.HeaderData]) -> list[FeatureSpec]:
    specs: list[FeatureSpec] = []

    # 视频：每台设备使用自己的 PatchesXXXX 表；Resolver 证据共用。
    for header_name, namespace, os_name in (
        ('UserSequoiaVideoData.hpp', 'UserSequoiaVideoData', 'Darwin24'),
        ('UserVideoDecodeData.hpp', 'UserVideoDecodeData', 'Darwin25'),
    ):
        header = headers[header_name]
        for device, table in _device_patches(header, namespace):
            specs.append(
                FeatureSpec(
                    key=f'Vcn2Decode_{os_name}_{device:04X}',
                    feature='Vcn2Decode',
                    os=(os_name,),
                    device_id=device,
                    evidence='RelocationAwareFullFunction',
                    header=header_name,
                    namespace=namespace,
                    patches=table,
                    functions='ResolverFunctions',
                    locations='ResolverLocations',
                    uuid_namespace=namespace,
                    uuid_name='CacheUUID',
                )
            )

    # Sequoia 显示组：每台设备一套补丁表，共用完整函数证据。
    header = headers['UserSequoiaSurfaceData.hpp']
    for device, table in _device_patches(header, 'UserSequoiaSurfaceData'):
        specs.append(
            FeatureSpec(
                key=f'DisplaySRD_Darwin24_{device:04X}',
                feature='DisplaySRD',
                os=('Darwin24',),
                device_id=device,
                evidence='RelocationAwareFullFunction',
                header='UserSequoiaSurfaceData.hpp',
                namespace='UserSequoiaSurfaceData',
                patches=table,
                functions='ResolverFunctions',
                locations='ResolverLocations',
                uuid_namespace='UserSequoiaSurfaceData',
                uuid_name='CacheUUID',
            )
        )

    # Scratch 与 SubmitGuard 的提交入口只提供函数证据；实际补丁由设备表/运行时生成。
    specs.append(
        FeatureSpec(
            key='DisplayComputeScratch_Darwin24_25',
            feature='DisplayComputeScratch',
            os=('Darwin24', 'Darwin25'),
            device_id=0,
            evidence='RelocationAwareFullFunction',
            header='UserSurfaceSyncData.hpp',
            namespace='UserSurfaceSyncData::ScratchResolverData',
            patches=None,
            functions='ResolverFunctions',
            locations='ResolverLocations',
            uuid_namespace='UserSurfaceSyncData',
            uuid_name='CacheUUID',
        )
    )
    specs.append(
        FeatureSpec(
            key='DisplayPipelineConstructor_Darwin24_25',
            feature='DisplayPipelineConstructor',
            os=('Darwin24', 'Darwin25'),
            device_id=0,
            evidence='RelocationAwareFullFunction',
            header='UserSurfaceConstructorData.hpp',
            namespace='UserSurfaceConstructorData',
            patches='Patches',
            functions='ResolverFunctions',
            locations='ResolverLocations',
            uuid_namespace='UserSurfaceSyncData',
            uuid_name='CacheUUID',
        )
    )
    specs.append(
        FeatureSpec(
            key='DisplaySubmit_Darwin24_25',
            feature='DisplaySubmit',
            os=('Darwin24', 'Darwin25'),
            device_id=0,
            evidence='RelocationAwareFullFunction',
            header='UserSurfaceConstructorData.hpp',
            namespace='UserSurfaceConstructorData::Submit',
            patches=None,
            functions='ResolverFunctions',
            locations='ResolverLocations',
            uuid_namespace='UserSurfaceSyncData',
            uuid_name='CacheUUID',
        )
    )

    # Darwin 25 的静态 SRD / LegacyBlend 表没有完整函数证据，按已知构建+完整补丁字节描述。
    for key, feature, table in (
        ('DisplaySRD_Darwin25', 'DisplaySRD', 'SrdSharedPatches'),
        ('DisplayLegacyBlend_Darwin25', 'DisplayLegacyBlend', 'LegacyBlendPatches'),
    ):
        specs.append(
            FeatureSpec(
                key=key,
                feature=feature,
                os=('Darwin25',),
                device_id=0,
                evidence='ExactValidatedBuild',
                header='UserSurfaceSyncData.hpp',
                namespace='UserSurfaceSyncData',
                patches=table,
                functions=None,
                locations=None,
                uuid_namespace='UserSurfaceSyncData',
                uuid_name='CacheUUID',
            )
        )
    return specs


def _feature(spec: FeatureSpec, headers: dict[str, cppdata.HeaderData]) -> dict:
    header = headers[spec.header]
    namespace = spec.namespace
    images = _parse_images(header, namespace)
    functions = _parse_functions(header, namespace, images) if spec.functions else []
    locations = _parse_locations(header, namespace) if spec.locations else []
    patches: list[dict] = []
    if spec.patches:
        base = None
        if spec.patches != 'Patches' and header.maybe_array(namespace, 'Patches') is not None:
            base = _parse_patches(header, namespace, 'Patches', None)
        patches = _parse_patches(header, namespace, spec.patches, base)

    for index, patch in enumerate(patches):
        location = locations[index] if index < len(locations) else None
        patch['function'] = location['function'] if location else None
        patch['instructionOffset'] = location['offset'] if location else None

    return {
        'key': spec.key,
        'feature': spec.feature,
        'os': list(spec.os),
        'deviceID': spec.device_id,
        'evidence': spec.evidence,
        'source': spec.header,
        'cacheUUID': _uuid(header, spec.uuid_namespace, spec.uuid_name),
        'images': images,
        'functions': functions,
        'locations': locations,
        'patches': patches,
    }


def _validate_refs(function: dict, refs: list[dict], images: int, context: str) -> None:
    for index, ref in enumerate(refs):
        if ref['offset'] < 0 or ref['offset'] + 4 > function['size']:
            raise ValueError(f'{context}: 引用 {index} 越出函数')
        if ref['image'] < 0 or ref['image'] >= images:
            raise ValueError(f'{context}: 引用 {index} 映像索引越界')
        if ref['rva'] < 0 or ref['rva'] >= schema.USER_LIMIT:
            raise ValueError(f'{context}: 引用 {index} 的 RVA 越界')
        if ref['indirect'] not in (0, 1, 2):
            raise ValueError(f'{context}: 引用 {index} 的 indirect 类型非法')


def validate(feature: dict) -> None:
    if feature['feature'] not in schema.FEATURE_IDS:
        raise ValueError(f"未知功能: {feature['feature']}")
    if feature['evidence'] not in schema.EVIDENCE_KINDS:
        raise ValueError(f"未知证据等级: {feature['evidence']}")
    for name in feature['os']:
        if name not in schema.OS_BITS:
            raise ValueError(f'未知 Darwin 掩码: {name}')
    images, functions = feature['images'], feature['functions']
    locations, patches = feature['locations'], feature['patches']
    if len(images) > MAX_IMAGES or len(functions) > MAX_FUNCTIONS or len(locations) > MAX_SITES:
        raise ValueError(f"{feature['key']}: 映像/函数/位置预算超限")
    if len(patches) > MAX_SITES:
        raise ValueError(f"{feature['key']}: 补丁预算超限")
    # Scratch/Submit 只有函数证据、没有静态补丁表；有补丁时必须与位置一一对应。
    if patches and locations and len(locations) != len(patches):
        raise ValueError(f"{feature['key']}: 位置表与补丁表长度不一致")

    for index, function in enumerate(functions):
        if not 32 <= function['size'] <= 65536:
            raise ValueError(f"{feature['key']}: 函数 {index} 长度越界")
        if function['image'] < 0 or function['image'] >= len(images):
            raise ValueError(f"{feature['key']}: 函数 {index} 映像索引越界")
        if function['rva'] < 0 or function['rva'] >= schema.USER_LIMIT:
            raise ValueError(f"{feature['key']}: 函数 {index} RVA 越界")
        _validate_refs(function, function['originalRefs'], len(images), f"{feature['key']} 函数 {index} 原生")
        _validate_refs(function, function['patchedRefs'], len(images), f"{feature['key']} 函数 {index} 已补丁")

    intervals: dict[int, list[tuple[int, int]]] = {}
    static_intervals: list[tuple[int, int]] = []
    for index, patch in enumerate(patches):
        size = patch['size']
        if not 0 < size <= MAX_PATCH_SIZE:
            raise ValueError(f"{feature['key']}: 补丁 {index} 长度越界")
        if len(patch['_original']) != size or len(patch['_patched']) != size:
            raise ValueError(f"{feature['key']}: 补丁 {index} 字节长度不一致")
        if patch['referenceOffset'] < 0 or patch['referenceOffset'] >= schema.USER_LIMIT:
            raise ValueError(f"{feature['key']}: 补丁 {index} 参考偏移越界")
        if patch['function'] is None:
            static_intervals.append((patch['referenceOffset'], patch['referenceOffset'] + size))
            continue
        function_index = patch['function']
        if function_index < 0 or function_index >= len(functions):
            raise ValueError(f"{feature['key']}: 补丁 {index} 函数索引越界")
        function = functions[function_index]
        offset = patch['instructionOffset']
        if offset is None or offset < 0 or offset + size > function['size']:
            raise ValueError(f"{feature['key']}: 补丁 {index} 指令范围越界")
        if patch['_original'] != function['_original'][offset:offset + size]:
            raise ValueError(f"{feature['key']}: 补丁 {index} 原始字节不属于目标函数")
        intervals.setdefault(function_index, []).append((offset, offset + size))

    for function_index, ranges in intervals.items():
        ranges.sort()
        for previous, current in zip(ranges, ranges[1:]):
            if current[0] < previous[1]:
                raise ValueError(f"{feature['key']}: 函数 {function_index} 的补丁重叠")

    static_intervals.sort()
    for previous, current in zip(static_intervals, static_intervals[1:]):
        if current[0] < previous[1]:
            raise ValueError(f"{feature['key']}: 静态补丁重叠")


def _strip(feature: dict) -> dict:
    result = dict(feature)
    result['functions'] = [
        {key: value for key, value in function.items() if not key.startswith('_')} for function in feature['functions']
    ]
    result['patches'] = [
        {
            **{key: value for key, value in patch.items() if not key.startswith('_')},
            'original': patch['_original'].hex(),
            'patched': patch['_patched'].hex(),
        }
        for patch in feature['patches']
    ]
    return result


def _cstr(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _sanitize(value: str) -> str:
    return re.sub(r'[^A-Za-z0-9_]', '_', value)


def _os_expression(names: list[str]) -> str:
    return ' | '.join(f'Os{name}' for name in names)


def emit_cpp(features: list[dict]) -> str:
    unique_headers = [name for name in HEADERS if any(feature['source'] == name for feature in features)]
    lines = [
        '// 由 tools/vpf/generate_manifest.py 生成，不手工编辑。',
        '#pragma once',
        '#include <VPF/PatchManifest.hpp>',
    ]
    lines += [f'#include <{name}>' for name in unique_headers]
    lines += ['', 'namespace VPF', '{', 'namespace ManifestData', '{', '']

    for feature in features:
        if not feature['patches']:
            continue
        namespace = next(
            spec.namespace
            for spec in _spec_map()[feature['key']]
        )
        patches = f"{namespace}::{_patch_table(feature)}"
        name = f"Sites_{_sanitize(feature['key'])}"
        if feature['locations']:
            lines.append(f'inline constexpr auto {name} = makeSites({patches}, {namespace}::ResolverLocations);')
        else:
            lines.append(f'inline constexpr auto {name} = makeSites({patches});')
        feature['_sites'] = f'{name}.sites'
    lines.append('')

    lines.append('inline constexpr FeatureManifest Features[] = {')
    for feature in features:
        spec = _spec_map()[feature['key']][0]
        namespace = spec.namespace
        images = (f"{namespace}::ResolverImages, {len(feature['images'])}" if feature['images'] else 'nullptr, 0')
        functions = (
            f"{namespace}::ResolverFunctions, {len(feature['functions'])}" if feature['functions'] else 'nullptr, 0'
        )
        locations = (
            f"{namespace}::ResolverLocations, {len(feature['locations'])}" if feature['locations'] else 'nullptr, 0'
        )
        sites = f"{feature['_sites']}, {len(feature['patches'])}" if feature['patches'] else 'nullptr, 0'
        uuid = _cstr(feature['cacheUUID']) if feature['cacheUUID'] else 'nullptr'
        lines.append(
            f'    {{FeatureId::{feature["feature"]}, {_os_expression(feature["os"])}, '
            f'0x{feature["deviceID"]:04X}, EvidenceKind::{feature["evidence"]},\n'
            f'     {{{images}, {functions}, {locations}, {sites}}},\n'
            f'     {_cstr(feature["source"])}, {uuid}}},'
        )
    lines += [
        '};',
        'inline constexpr UInt32 FeatureCount = sizeof(Features) / sizeof(Features[0]);',
        'static_assert(FeatureCount <= 64);',
        'static_assert(validAll(Features));',
        '',
        'inline constexpr const FeatureManifest* find(FeatureId feature, UInt32 osMask, UInt32 deviceID = 0)',
        '{',
        '    return VPF::find(Features, FeatureCount, feature, osMask, deviceID);',
        '}',
        '',
        '}    // namespace ManifestData',
        '}    // namespace VPF',
        '',
    ]
    return '\n'.join(lines)


_SPECS_CACHE: dict[str, list[FeatureSpec]] | None = None


def _spec_map() -> dict[str, list[FeatureSpec]]:
    global _SPECS_CACHE
    if _SPECS_CACHE is None:
        headers = {name: cppdata.parse(ROOT / 'NootedRed' / name) for name in HEADERS}
        _SPECS_CACHE = {spec.key: [spec] for spec in _specs(headers)}
    return _SPECS_CACHE


def _patch_table(feature: dict) -> str:
    return _spec_map()[feature['key']][0].patches


def build(features: list[dict]) -> list[dict]:
    for feature in features:
        validate(feature)
    keys = [feature['key'] for feature in features]
    if len(keys) != len(set(keys)):
        raise ValueError('功能键重复')
    return features


def build_all() -> list[dict]:
    headers = {name: cppdata.parse(ROOT / 'NootedRed' / name) for name in HEADERS}
    global _SPECS_CACHE
    _SPECS_CACHE = {spec.key: [spec] for spec in _specs(headers)}
    features = [_feature(spec, headers) for spec in _specs(headers)]
    return build(features)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='确认生成头与当前数据一致')
    parser.add_argument('--write', action='store_true', help='写出 NootedRed/VPF/PatchManifestData.hpp')
    parser.add_argument('--json', type=Path, help='写出规范化 Manifest JSON')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args()

    features = build_all()
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            schema.canonical_json(
                {
                    'schemaVersion': schema.SCHEMA_VERSION,
                    'kind': 'vpf.manifest',
                    'features': [_strip(feature) for feature in features],
                }
            )
        )
    text = emit_cpp(features)
    if args.check:
        if not OUTPUT.exists():
            print(f'缺少生成头: {OUTPUT}', file=sys.stderr)
            return 1
        if OUTPUT.read_text() != text:
            print(f'生成头已陈旧或被手改: {OUTPUT}\n运行 python3 tools/vpf/generate_manifest.py --write', file=sys.stderr)
            return 1
    if args.write:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(text)
    if not args.quiet:
        print(f'VPF Manifest: {len(features)} 个功能实例，{sum(len(f["patches"]) for f in features)} 个补丁站点')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
