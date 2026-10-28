"""冻结 DVPF 显示基线：源码/夹具哈希、补丁字节、设备白名单和验证边界。

输出 tools/dvpf/fixtures/display-baseline.json 与 docs/dvpf-baseline.md；
两者都必须提交，`--check` 用于确认没有陈旧或被手改。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vpf'))

import display_data
import display_schema as schema

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / 'tools/dvpf/fixtures/display-baseline.json'
DOCUMENT = ROOT / 'docs/dvpf-baseline.md'

BASELINE_COMMIT = '31fcc872807ae06f59d99016dd5ef80d8996e8ec'
DESIGN_COMMIT = '6fb1aa6df07243c44541cbd3ccf5371804c4a804'

TRACKED = (
    'NootedRed/UserOpenGL.cpp',
    'NootedRed/UserOpenGLResolver.hpp',
    'NootedRed/UserOpenGLData.hpp',
    'NootedRed/UserSurfaceSync.cpp',
    'NootedRed/UserSurfaceSyncResolver.hpp',
    'NootedRed/UserSurfaceSyncData.hpp',
    'NootedRed/UserSequoiaSurfaceData.hpp',
    'NootedRed/UserSurfaceConstructorData.hpp',
    'NootedRed/UserSubmitGuardData.hpp',
    'NootedRed/UserLegacyData.hpp',
    'NootedRed/UserSharedCache.hpp',
    'NootedRed/UserCacheResolver.hpp',
    'NootedRed/UserPatchMemory.hpp',
    'NootedRed/UserPatchMapGate.hpp',
    'NootedRed/UserPatchCoordinator.cpp',
    'NootedRed/UserPatchCoordinator.hpp',
    'NootedRed/UserVideoDecode.cpp',
    'NootedRed/NRed.cpp',
    'NootedRed/VPF/MetalTransactionExecutor.hpp',
    'NootedRed/VPF/VerifiedPatchPlan.hpp',
    'NootedRed/VPF/DisplayPlan.hpp',
    'NootedRed/VPF/DisplayPlanAdapters.hpp',
    'NootedRed/VPF/DisplayManifestData.hpp',
    'NootedRed/VPF/GLManifestData.hpp',
    'NootedRed/VPF/ValidatedPageBackend.hpp',
    'NootedRed/VPF/ExecutableTrampolineResource.hpp',
    'NootedRed/KernelDisplayEvidence.hpp',
    'NootedRed/KernelDisplayEvidence.cpp',
    'NootedRed/X5000.cpp',
    'NootedRed/X6000FB.cpp',
    'NootedRed/AMDGFX9DCNDisplay.cpp',
    'tools/build_opengl_patch.py',
    'tools/build_surface_sync_patch.py',
    'tools/build_submit_guard.py',
    'tools/build_sequoia_surface.py',
    'tools/build_cache_section_targets.py',
    'tools/import_legacy_compat.py',
    'tools/vpf/build_gl_evidence.py',
    'tools/vpf/gl_evidence.py',
    'tools/vpf/analyze_display_variants.py',
    'tools/vpf/display_semantics.py',
    'tools/dvpf/gl_page_adapter_probe.cpp',
    'tools/vpf/gl_dynamic_variants_probe.cpp',
    'tools/fixtures/opengl-compat.json',
    'tools/fixtures/legacy-compat.json',
    'tools/fixtures/sequoia-compat.json',
)

TEST_COMMANDS = (
    'python3 tools/build_opengl_patch.py --check',
    'python3 tools/vpf/generate_manifest.py --check',
    'python3 tools/vpf/validate_manifest.py',
    'python3 tools/dvpf/generate_display_manifest.py --check',
    'python3 tools/test_display_baseline.py',
    'python3 tools/test_display_manifest.py',
    'python3 tools/test_display_plan_equivalence.py',
    'python3 tools/test_display_capability_state.py',
    'python3 tools/test_display_trampoline_lifecycle.py',
    'python3 tools/test_display_cross_module_gate.py',
    'python3 tools/test_display_transaction_executor.py',
    'python3 tools/test_vpf_gl_page_adapter.py',
    'python3 tools/test_vpf_gl_dynamic_variants.py',
    'python3 tools/test_vpf_gl_mounted_evidence.py',
    'python3 tools/test_kernel_display_evidence.py',
    'python3 tools/test_vpf_display_semantic_variants.py',
    'python3 tools/vpf/build_gl_evidence.py --fixtures --check',
    'python3 tools/vpf/analyze_display_variants.py --fail-on-rejected',
    'python3 tools/test_vpf_manifest.py',
    'python3 tools/test_vpf_plan.py',
    'python3 tools/test_vpf_baseline.py',
    'python3 tools/test_vpf_feature_state.py',
    'python3 tools/test_vpf_lifecycle.py',
)

_PROPERTY = re.compile(r'setProp32\("(NRed[A-Za-z0-9]+)"')
_KERNEL_PROPERTIES = (
    'NRedKernelComputeVTable', 'NRedKernelDisplayEvidenceRevision', 'NRedKernelFlipFormat0',
    'NRedKernelFlipFormat1', 'NRedKernelFlipFormat2', 'NRedKernelFlipFormat3', 'NRedKernelFlipPitch0',
    'NRedKernelFlipPitch1', 'NRedKernelFlipPitch2', 'NRedKernelFlipPitch3', 'NRedKernelFlipRotation0',
    'NRedKernelFlipRotation1', 'NRedKernelFlipRotation2', 'NRedKernelFlipRotation3', 'NRedKernelFlipWaitCount',
    'NRedKernelFlipWaitMax', 'NRedKernelFlipWaitTotal', 'NRedKernelFramebufferDarwin',
    'NRedKernelFramebufferFlags', 'NRedKernelGFX9Attributes', 'NRedKernelGFX9DeviceID',
    'NRedKernelGFX9FbOffsetHigh', 'NRedKernelGFX9FbOffsetLow', 'NRedKernelGFX9Revisions',
    'NRedKernelKextError', 'NRedKernelKextValid', 'NRedKernelScanoutChanges', 'NRedKernelScanoutFormat0',
    'NRedKernelScanoutFormat1', 'NRedKernelScanoutFormat2', 'NRedKernelScanoutFormat3',
    'NRedKernelScanoutSwizzle0', 'NRedKernelScanoutSwizzle1', 'NRedKernelScanoutSwizzle2',
    'NRedKernelScanoutSwizzle3', 'NRedKernelX5000KextSize', 'NRedKernelX5000KextUUID0',
    'NRedKernelX5000KextUUID1', 'NRedKernelX5000KextUUID2', 'NRedKernelX5000KextUUID3',
    'NRedKernelX5000SourceVersionHigh', 'NRedKernelX5000SourceVersionLow', 'NRedKernelX6000FBKextSize',
    'NRedKernelX6000FBKextUUID0', 'NRedKernelX6000FBKextUUID1', 'NRedKernelX6000FBKextUUID2',
    'NRedKernelX6000FBKextUUID3', 'NRedKernelX6000FBSourceVersionHigh',
    'NRedKernelX6000FBSourceVersionLow',
)


def _properties() -> list[str]:
    values = set(_KERNEL_PROPERTIES)
    for relative in ('NootedRed/UserSurfaceSync.cpp', 'NootedRed/UserOpenGL.cpp',
                     'NootedRed/UserPatchCoordinator.cpp'):
        values.update(_PROPERTY.findall((ROOT / relative).read_text()))
    return sorted(values)


def _feature_key(feature: dict) -> str:
    return f"{feature['feature']}/{feature['osMask']:X}/{feature['deviceID']:04X}"


def _site_hashes(feature: dict) -> list[dict]:
    result = []
    for index, site in enumerate(feature['sites'] + feature['sharedSites']):
        result.append(
            {
                'index': index,
                'offset': site['offset'],
                'size': site['size'],
                'originalSha256': schema.format_digest(site['original']),
                'replacementSha256': schema.format_digest(site['replacement']),
                'functionIndex': site['functionIndex'],
                'instructionOffset': site['instructionOffset'],
                'dynamicOffset': site['dynamicOffset'],
                'dynamicSize': site['dynamicSize'],
                'pageFileOffset': site.get('pageFileOffset'),
                'pageOffset': site.get('pageOffset'),
            }
        )
    return result


def build_baseline() -> dict:
    features = display_data.build_all()
    stripped = [display_data.strip(feature) for feature in features]
    return {
        'schemaVersion': schema.SCHEMA_VERSION,
        'kind': 'dvpf.baseline',
        'baselineCommit': BASELINE_COMMIT,
        'designBaselineCommit': DESIGN_COMMIT,
        'sources': {relative: schema.file_digest(ROOT / relative) for relative in TRACKED},
        'bootArgs': {
            'enabled': [schema.DISPLAY_FIX_ARG],
            'inactive': list(schema.INACTIVE_ARGS),
        },
        'properties': _properties(),
        'deviceWhitelist': display_data.supported_devices(),
        'features': stripped,
        'featureDigests': {_feature_key(feature): schema.digest(stripped[index])
                           for index, feature in enumerate(features)},
        'siteHashes': {feature['key']: _site_hashes(feature) for feature in features},
        'hardware': schema.HARDWARE_BASELINE,
        'testCommands': list(TEST_COMMANDS),
    }


def _md_escape(value: str) -> str:
    return value.replace('|', '\\|')


def render_markdown(baseline: dict) -> str:
    lines = [
        '# DVPF 显示基线',
        '',
        f'> 冻结基线：`{baseline["baselineCommit"]}`；参考代码基线：`{baseline["designBaselineCommit"]}`。',
        '> 本文件由 `python3 tools/dvpf/freeze_display_baseline.py --write` 生成，不手工编辑。',
        '',
        '本基线记录显示补丁字节、解析器、设备白名单、启动参数和源码哈希。',
        '离线证据和用户态测试不改变运行时准入。',
        '',
        '## 1. 不变量',
        '',
        '- 唯一显示总开关是 `-NRedDisplayFix`；`-NRedImmediateSync`、`-NRedSrdShared`、`-NRedLegacyBlend`、',
        '  `-NRedComputeScratch` 保持停用。',
        '- OpenGL 只修改当前已验证的 4 KiB 页内完整块；Metal 只使用当前 task 的 `vm_map` 和私有 COW 页；',
        '  内核 GFX/DCN 不套用用户态事务。',
        '- SubmitGuard 的入口跳板和守卫页生命周期独立于普通渲染组；入口无法确认恢复时保留目标页。',
        '- 本基线中的 `Applied` 标记不等同于 GPU 执行或 HDR 显示已验收。',
        '',
        '## 2. 源码与夹具快照',
        '',
        '| 文件 | SHA-256 |',
        '| --- | --- |',
    ]
    lines += [f'| `{name}` | `{baseline["sources"][name]}` |' for name in sorted(baseline['sources'])]
    lines += [
        '',
        '## 3. 启动参数与诊断属性',
        '',
        f"- 启用：`{baseline['bootArgs']['enabled'][0]}`",
        f"- 停用：{', '.join(f'`{value}`' for value in baseline['bootArgs']['inactive'])}",
        '',
        '显示路径当前发布的属性（仅诊断，不代表安装成功）：',
        '',
    ]
    lines += [f'- `{value}`' for value in baseline['properties']]
    lines += [
        '',
        '## 4. 设备白名单',
        '',
        '来自 `NRed.cpp` 的 APU 分类：',
        '',
        ', '.join(f'`0x{value:04X}`' for value in baseline['deviceWhitelist']),
        '',
        '## 5. 显示功能清单',
        '',
        '| 功能 | Domain | Group | Darwin | Device | Evidence | 站点 | 最大字节 | 来源 |',
        '| --- | --- | --- | --- | ---: | --- | ---: | ---: | --- |',
    ]
    for feature in baseline['features']:
        sites = len(feature['sites']) + len(feature['sharedSites'])
        lines.append(
            f"| `{feature['feature']}` | {feature['domain']} | {feature['group']} | "
            f"{'/'.join(name[6:] for name in feature['os'])} | 0x{feature['deviceID']:04X} | "
            f"{feature['evidence']} | {sites} | {feature['maxPatchedBytes']} | `{feature['source']}` |"
        )
    lines += [
        '',
        '## 6. 补丁站点与字节摘要',
        '',
        '站点顺序与生成头一致；完整原始/替换字节见基线 JSON。OpenGL 站点另记录参考页范围与',
        '相对引用目标；Metal 站点的完整函数/段节证据由 `VPF::ManifestData` 和',
        '`tools/vpf/fixtures/baseline.json` 保存。',
        '',
        '| Key | # | 文件/函数偏移 | 大小 | 原始 SHA-256 | 替换 SHA-256 | 函数 | 指令偏移 | 页范围 |',
        '| --- | ---: | ---: | ---: | --- | --- | ---: | ---: | --- |',
    ]
    for feature in baseline['features']:
        sites = feature['sites'] + feature['sharedSites']
        hashes = baseline['siteHashes'][feature['key']]
        for index, site in enumerate(sites):
            page = ''
            if site.get('pageFileOffset') is not None:
                page = f"0x{site['pageFileOffset']:X}+0x{site['pageOffset']:X}"
            lines.append(
                f"| `{_md_escape(feature['feature'])}` | {index} | `0x{site['offset']:X}` | {site['size']} | "
                f"`{hashes[index]['originalSha256'][:16]}` | "
                f"`{hashes[index]['replacementSha256'][:16]}` | {site['functionIndex']} | "
                f"`0x{site['instructionOffset']:X}` | {page} |"
            )
    lines += [
        '',
        'OpenGL 相对引用（原生/已修复）：',
        '',
    ]
    for feature in baseline['features']:
        if not feature['sites'] or not feature['refs']:
            continue
        refs = feature['refs']
        original = ', '.join(f"{ref['offset']}→0x{ref['rva']:X}" for ref in refs.get('original', []))
        patched = ', '.join(f"{ref['offset']}→0x{ref['rva']:X}" for ref in refs.get('patched', []))
        lines.append(f"- `{feature['feature']}/{feature['os'][0]}/0x{feature['deviceID']:04X}`：原生 [{original}]；"
                     f"已修复 [{patched}]")
    lines += [
        '',
        '## 7. 实机验证边界',
        '',
        f"- {baseline['hardware']['status']}",
        '',
        '## 8. 新旧计划对照基座',
        '',
        '- 共享缓存：`tools/vpf/plan_probe.cpp` + `tools/vpf/compare_plan.py` +',
        '  `tools/vpf/fixtures/baseline.json`。',
        '- OpenGL 页计划：`tools/dvpf/display_plan_probe.cpp` + `tools/dvpf/compare_display_plan.py`。',
        '- 清单与生成头：`python3 tools/dvpf/generate_display_manifest.py --check`。',
        '- 逐项等价：`python3 tools/test_display_plan_equivalence.py`；字节、地址、函数引用和状态必须一致。',
        '',
        '## 9. 验证命令',
        '',
        '```sh',
    ]
    lines += list(baseline['testCommands'])
    lines += ['```', '']
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='写出基线与审计报告')
    parser.add_argument('--check', action='store_true', help='确认基线与审计报告未陈旧')
    args = parser.parse_args()
    baseline = build_baseline()
    json_text = schema.canonical_json(baseline)
    markdown = render_markdown(baseline)
    if args.check:
        failures = []
        if not BASELINE.exists() or BASELINE.read_text() != json_text:
            failures.append(str(BASELINE))
        if not DOCUMENT.exists() or DOCUMENT.read_text() != markdown:
            failures.append(str(DOCUMENT))
        if failures:
            print('DVPF 基线已陈旧或被手改: ' + ', '.join(failures) +
                  '\n运行 python3 tools/dvpf/freeze_display_baseline.py --write', file=sys.stderr)
            return 1
    if args.write:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json_text)
        DOCUMENT.parent.mkdir(parents=True, exist_ok=True)
        DOCUMENT.write_text(markdown)
    print(f'DVPF 基线: {len(baseline["features"])} 个功能实例，'
          f'{sum(len(feature["sites"]) + len(feature["sharedSites"]) for feature in baseline["features"])} 个站点')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
