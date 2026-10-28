"""VPF：补丁站点分类与内核补丁入口清点；只报告，不删除或替换任何 PatchSite。"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/vpf'))

import generate_manifest

GROUPS = {
    'Vcn2Decode': 'vcn-applegva',
    'Vcn2SafetyGuard': 'vcn-applegva',
    'Vcn2Encode': 'vcn-applegva',
    'AppleGVASelection': 'vcn-applegva',
    'FairPlayNativeDecodeSubset': 'vcn-applegva',
    'DisplaySRD': 'metal-display',
    'DisplayLegacyBlend': 'metal-display',
    'DisplayComputeScratch': 'metal-display',
    'DisplayPipelineConstructor': 'metal-display',
    'DisplaySubmit': 'metal-display',
    'DisplaySubmitGuard': 'submit-guard',
    'Vcn2HEVCMain10': 'vcn-main10',
}

CATEGORY_NOTES = {
    'single-byte': '单字节立即数/控制位；不能仅凭长度推断语义。',
    'small-field': '≤4 字节字段；不能仅凭大小推断可替换。',
    'instruction-sequence': '5–8 字节指令序列；通常需要函数/ABI 证明，暂不作为去量化候选。',
    'block': '>8 字节代码块；保留为 PatchSite，除非有完整替代接口证明。',
    'static-table': '静态表/入口补丁；没有完整函数证据，不能按动态函数变体处理。',
}


def category(patch: dict) -> str:
    if patch['function'] is None:
        return 'static-table'
    if patch['size'] == 1:
        return 'single-byte'
    if patch['size'] <= 4:
        return 'small-field'
    if patch['size'] <= 8:
        return 'instruction-sequence'
    return 'block'


def overlaps(reference: dict, offset: int, size: int) -> bool:
    return reference['offset'] < offset + size and offset < reference['offset'] + 4


def _kernel_inventory() -> list[dict]:
    """只读清点内核补丁入口；不证明符号/ABI 可替代，也不修改源码。"""
    patterns = {
        'MaskedLookupPatch': r'\bMaskedLookupPatch\b',
        'findAndReplaceWithMask': r'\bfindAndReplaceWithMask\b',
        'PatternRouteRequest': r'\bPatternRouteRequest\b',
        'PatternSolveRequest': r'\bPatternSolveRequest\b',
    }
    inventory = []
    for name in ('X5000.cpp', 'X6000FB.cpp', 'AMDGFX9DCNDisplay.cpp'):
        path = ROOT / 'NootedRed' / name
        text = path.read_text()
        inventory.append(
            {
                'file': name,
                'markers': {label: len(re.findall(pattern, text)) for label, pattern in patterns.items()},
                'status': 'KernelPatcher/ABI 路径；不属于用户态 PatchSite，本审计不替换。',
            }
        )
    return inventory


def build_report(features: list[dict]) -> dict:
    feature_reports = []
    total_categories = Counter()
    group_counts = Counter()
    candidates = []
    for feature in features:
        functions = feature['functions']
        categories = Counter()
        short = []
        for index, patch in enumerate(feature['patches']):
            kind = category(patch)
            categories[kind] += 1
            total_categories[kind] += 1
            group = GROUPS.get(feature['feature'], 'other')
            group_counts[group] += 1
            typed = False
            if patch['function'] is not None:
                typed = any(overlaps(reference, patch['instructionOffset'], patch['size'])
                            for reference in functions[patch['function']]['originalRefs'])
            if patch['size'] <= 4 and patch['function'] is not None:
                item = {
                    'feature': feature['key'],
                    'group': group,
                    'index': index,
                    'offset': patch['instructionOffset'],
                    'size': patch['size'],
                    'category': kind,
                    'typedReference': typed,
                }
                short.append(item)
                candidates.append(item)
        feature_reports.append(
            {
                'key': feature['key'],
                'feature': feature['feature'],
                'group': GROUPS.get(feature['feature'], 'other'),
                'os': feature['os'],
                'deviceID': feature['deviceID'],
                'sites': len(feature['patches']),
                'categories': dict(sorted(categories.items())),
                'reviewCandidates': short,
            }
        )

    return {
        'kind': 'vpf.patch-site-audit',
        'schemaVersion': 1,
        'totalSites': sum(feature['sites'] for feature in feature_reports),
        'totalByCategory': dict(sorted(total_categories.items())),
        'totalByGroup': dict(sorted(group_counts.items())),
        'categoryNotes': CATEGORY_NOTES,
        'features': feature_reports,
        'reviewCandidates': candidates,
        'kernelAudit': _kernel_inventory(),
    }


def markdown(report: dict) -> str:
    lines = [
        '# VPF 补丁站点审计',
        '',
        f'- 站点总数：{report["totalSites"]}',
        f'- 分类：`{json.dumps(report["totalByCategory"], ensure_ascii=False, sort_keys=True)}`',
        f'- 分组：`{json.dumps(report["totalByGroup"], ensure_ascii=False, sort_keys=True)}`',
        '',
        '| 功能 | 分组 | 站点 | 分类 | 复核候选 |',
        '| --- | --- | ---: | --- | ---: |',
    ]
    for feature in report['features']:
        categories = ', '.join(f'{key}={value}' for key, value in feature['categories'].items())
        lines.append(f'| `{feature["key"]}` | {feature["group"]} | {feature["sites"]} | {categories} | '
                     f'{len(feature["reviewCandidates"])} |')
    lines += ['', '## 内核补丁入口清点', '', '| 文件 | MaskedLookupPatch | findAndReplaceWithMask | PatternRouteRequest | PatternSolveRequest |',
              '| --- | ---: | ---: | ---: | ---: |']
    for item in report['kernelAudit']:
        markers = item['markers']
        lines.append(f'| `{item["file"]}` | {markers["MaskedLookupPatch"]} | {markers["findAndReplaceWithMask"]} | '
                     f'{markers["PatternRouteRequest"]} | {markers["PatternSolveRequest"]} |')
    lines += ['', '内核路径是 KernelPatcher/ABI 事务，不是用户态 PatchSite；本审计不替换，'
              '仍需独立符号/ABI 证明和实机验收。', '']
    lines += ['', '## 说明', '',
              '- 复核候选只表示补丁 ≤4 字节；不代表可以安全替换。',
              '- 本报告不修改 PatchSite、生成头、Manifest 或运行时准入。']
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', type=Path, dest='json_output')
    parser.add_argument('--markdown', type=Path, dest='markdown_output')
    args = parser.parse_args()
    report = build_report(generate_manifest.build_all())
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + '\n')
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(markdown(report).rstrip() + '\n')
    print(f'VPF patch audit: {report["totalSites"]} sites, groups={report["totalByGroup"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
