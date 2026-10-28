"""从现有生成头和固定夹具确定性生成 DVPF 显示清单头。

只读取仓库内已审查的数组与夹具，不访问系统缓存，不重新生成补丁载荷。
生成结果必须提交；`--check` 用于测试确认没有陈旧或被手改。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vpf'))

import display_data
import display_schema as schema

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'NootedRed/VPF/DisplayManifestData.hpp'


def _cstr(value: str | None) -> str:
    if value is None:
        return 'nullptr'
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _sanitize(value: str) -> str:
    return re.sub(r'[^A-Za-z0-9_]', '_', value)


def _os_expression(names: list[str]) -> str:
    return ' | '.join(f'Os{name}' for name in names)


def _site_array(feature: dict) -> str:
    if not feature['sites']:
        return 'nullptr'
    name = f"PageSites_{_sanitize(feature['key'])}"
    lines = [f'inline constexpr DisplaySiteTemplate {name}[] = {{']
    for site in feature['sites']:
        if feature['feature'] == 'MetalSubmitGuard':
            lines.append(
                f'    {{0x{site["offset"]:X}ULL, {site["size"]}, UserSubmitGuardData::MethodOriginal, '
                f'SubmitGuardEntryTemplate, 6, 8, NoFunction, 0}},'
            )
        else:
            patch = site['cppPatch']
            location = site['cppLocation']
            lines.append(
                f'    {{{patch}.offset, {patch}.size, {patch}.original, {patch}.patched,\n'
                f'     0, 0, {site["functionIndex"]}, {location}}},'
            )
    lines.append('};')
    return '\n'.join(lines)


def _shared_sites(feature: dict) -> tuple[str, int]:
    if not feature['sharedSites']:
        return 'nullptr', 0
    if feature.get('cppSharedSites'):
        return feature['cppSharedSites'], len(feature['sharedSites'])
    name = f"VPF::ManifestData::Sites_{_sanitize(feature['key'])}.sites"
    return name, len(feature['sharedSites'])


def _trampoline(feature: dict) -> str:
    trampoline = feature['trampoline']
    if trampoline is None:
        return 'nullptr, 0, 0, 0'
    return (f"UserSubmitGuardData::Payload, {trampoline['size']}, {trampoline['dynamicOffset']}, "
            f"{trampoline['dynamicSize']}")


def emit_cpp(features: list[dict]) -> str:
    lines = [
        '// 由 tools/dvpf/generate_display_manifest.py 生成，不手工编辑。',
        '#pragma once',
        '#include <VPF/DisplayManifest.hpp>',
        '#include <VPF/PatchManifestData.hpp>',
        '#include <UserOpenGLData.hpp>',
        '#include <UserSubmitGuardData.hpp>',
        '#include <UserLegacyData.hpp>',
        '',
        'namespace VPF',
        '{',
        'namespace DisplayManifestData',
        '{',
        '',
    ]

    if any(feature['feature'] == 'MetalSubmitGuard' for feature in features):
        lines.append('inline constexpr UInt8 SubmitGuardEntryTemplate[] = {')
        lines.append('    0xFF, 0x25, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x90,')
        lines.append('};')
        lines.append('')

    for feature in features:
        if feature.get('cppSharedSites'):
            site = feature['sharedSites'][0]
            lines += [
                f'inline constexpr PatchSite {feature["cppSharedSites"]}[] = {{',
                f'    {{NoFunction, 0, {site["size"]}, UserLegacyData::OS23Gate, UserLegacyData::OS23GatePatched}},',
                '};',
                '',
            ]
        text = _site_array(feature)
        if text != 'nullptr':
            lines.append(text)
            lines.append('')

    lines.append('inline constexpr DisplayManifest Features[] = {')
    for feature in features:
        shared_sites, shared_count = _shared_sites(feature)
        page_sites = f"PageSites_{_sanitize(feature['key'])}" if feature['sites'] else 'nullptr'
        page_count = len(feature['sites'])
        shared_feature = (f"FeatureId::{feature['sharedFeature']}" if feature['sharedFeature'] else
                          'FeatureId::Count')
        lines.append(
            f'    {{DisplayFeature::{feature["feature"]}, DisplayDomain::{feature["domain"]}, '
            f'DisplayGroup::{feature["group"]},\n'
            f'     {_os_expression(feature["os"])}, 0x{feature["deviceID"]:04X}, '
            f'DisplayEvidence::{feature["evidence"]}, {shared_feature},\n'
            f'     0x{feature["requiredGroupMask"]:X}, 0x{feature["conflictGroupMask"]:X}, '
            f'{feature["maxSites"]}, {feature["maxPatchedBytes"]},\n'
            f'     {"true" if feature["pageLocalFallback"] else "false"}, '
            f'{"true" if feature["installable"] else "false"}, {_cstr(feature["imagePath"])}, '
            f'{_cstr(feature["source"])}, {_cstr(feature["cacheUUID"])},\n'
            f'     {shared_sites}, {shared_count}, {page_sites}, {page_count},\n'
            f'     {_trampoline(feature)}}},'
        )
    lines += [
        '};',
        'inline constexpr UInt32 FeatureCount = sizeof(Features) / sizeof(Features[0]);',
        'static_assert(FeatureCount <= 64);',
        'static_assert(validAll(Features));',
        '',
        'inline constexpr const DisplayManifest* find(DisplayFeature feature, UInt32 osMask, UInt32 deviceID = 0)',
        '{',
        '    return VPF::find(Features, FeatureCount, feature, osMask, deviceID);',
        '}',
        '',
        'inline constexpr const DisplayManifest* findOpenGL(UInt32 major, UInt32 deviceID, UInt32 kind)',
        '{',
        '    const auto feature = kind == 0 ? DisplayFeature::OpenGLHwInfo : DisplayFeature::OpenGLDualQuad;',
        '    return find(feature, displayOsMask(major), deviceID);',
        '}',
        '',
        '}    // namespace DisplayManifestData',
        '}    // namespace VPF',
        '',
    ]
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='确认生成头与当前数据一致')
    parser.add_argument('--write', action='store_true', help='写出 NootedRed/VPF/DisplayManifestData.hpp')
    parser.add_argument('--json', type=Path, help='写出规范化显示清单 JSON')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args()

    features = display_data.build_all()
    text = emit_cpp(features)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            schema.canonical_json(
                {
                    'schemaVersion': schema.SCHEMA_VERSION,
                    'kind': 'dvpf.manifest',
                    'features': [display_data.strip(feature) for feature in features],
                }
            )
        )
    if args.check:
        if not OUTPUT.exists():
            print(f'缺少生成头: {OUTPUT}', file=sys.stderr)
            return 1
        if OUTPUT.read_text() != text:
            print(f'生成头已陈旧或被手改: {OUTPUT}\n运行 python3 tools/dvpf/generate_display_manifest.py --write',
                  file=sys.stderr)
            return 1
    if args.write:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(text)
    if not args.quiet:
        print(f'DVPF Display Manifest: {len(features)} 个功能实例，'
              f'{sum(len(feature["sites"]) + len(feature["sharedSites"]) for feature in features)} 个站点')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
