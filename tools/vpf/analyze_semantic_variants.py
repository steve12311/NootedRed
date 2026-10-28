"""VPF：离线反汇编/CFG/块级 liveness 证据报告；不生成可安装 Manifest。

`--manifest` 汇总仓库内已生成函数的反汇编覆盖与拒绝原因；
`--reference/--candidate` 对两个离线 JSON 变体做严格比较。
任何接受结果都只是人工复核候选，`emittedManifests` 恒为 0。
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/vpf'))

import generate_manifest
import semantic_variants as sv
import x86_disasm as x86


def _write_json(path: Path | None, value: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n')


def _write_markdown(path: Path | None, text: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + '\n')


def _variant(feature: dict, index: int, function: dict) -> sv.FunctionVariant:
    image_index = function['image']
    image = feature['images'][image_index]['path'] if image_index < len(feature['images']) else ''
    references = tuple(
        sv.TypedReference(
            offset=reference['offset'],
            size=4,
            kind='rel32' if reference['indirect'] == 0 else 'indirect',
            target=reference['rva'],
            image=reference['image'],
            indirect=reference['indirect'],
        )
        for reference in function['originalRefs']
    )
    patches = tuple(
        sv.PatchConstraint(offset=patch['instructionOffset'], size=patch['size'], original=patch['_original'])
        for patch in feature['patches']
        if patch['function'] == index
    )
    return sv.FunctionVariant(
        name=f"{feature['key']}#{index}",
        image=image,
        code=function['_original'],
        references=references,
        patches=patches,
        device_id=feature['deviceID'],
        evidence=feature['evidence'],
    )


def _manifest_report(features: list[dict], limit: int | None = None) -> dict:
    feature_reports = []
    reasons: Counter[str] = Counter()
    disassembled = 0
    rejected = 0
    remaining = limit
    for feature in features:
        variants = []
        for index, function in enumerate(feature['functions']):
            if remaining is not None and remaining <= 0:
                break
            if remaining is not None:
                remaining -= 1
            variant = _variant(feature, index, function)
            try:
                disassembly = x86.disassemble(variant.code)
                status = {'status': 'ok', 'blocks': len(disassembly.blocks),
                          'instructions': len(disassembly.instructions)}
                disassembled += 1
            except x86.DisassemblyError as error:
                status = {'status': 'rejected', 'reason': str(error)}
                rejected += 1
                reason = str(error).split(':', 1)[0]
                reasons[reason] += 1
            variants.append(
                {
                    'name': variant.name,
                    'size': len(variant.code),
                    'normalizedDigest': sv.normalized_digest(variant),
                    'disassembly': status,
                }
            )
        feature_reports.append(
            {
                'key': feature['key'],
                'feature': feature['feature'],
                'os': feature['os'],
                'deviceID': feature['deviceID'],
                'variants': variants,
            }
        )
        if remaining is not None and remaining <= 0:
            break
    return {
        'kind': 'vpf.semantic-report',
        'schemaVersion': 1,
        'proofLevel': sv.PROOF_CFG,
        'emittedManifests': 0,
        'featureCount': len(feature_reports),
        'totalFeatureCount': len(features),
        'limitedTo': limit,
        'variantCount': disassembled + rejected,
        'disassembled': disassembled,
        'disassemblyRejected': rejected,
        'rejectionReasons': dict(sorted(reasons.items())),
        'features': feature_reports,
        'limitations': [
            '只支持可静态解码的 x86-64 指令；间接控制流、未知指令和无法解析的操作数一律拒绝。',
            'liveness 是块级数据流，caller-saved 按 ABI 保守处理；不是完整编译器语义证明。',
            '没有实时缓存/dyld 读取；运行时唯一性、设备和 ABI 约束仍需独立核验。',
            '报告不生成可安装 Manifest，不改变运行时准入。',
        ],
    }


def _manifest_markdown(report: dict) -> str:
    lines = [
        '# VPF 语义变体证据报告',
        '',
        f'- 证明等级：`{report["proofLevel"]}`',
        f'- 功能实例：{report["featureCount"]}；函数变体：{report["variantCount"]}',
        f'- 可反汇编：{report["disassembled"]}；拒绝：{report["disassemblyRejected"]}',
        f'- 生成的可安装 Manifest：{report["emittedManifests"]}',
        f'- 拒绝原因：`{json.dumps(report["rejectionReasons"], ensure_ascii=False, sort_keys=True)}`',
        '',
        '| 功能 | 变体 | 可反汇编 | 拒绝 |',
        '| --- | ---: | ---: | ---: |',
    ]
    for feature in report['features']:
        accepted = sum(1 for variant in feature['variants'] if variant['disassembly']['status'] == 'ok')
        lines.append(f'| `{feature["key"]}` | {len(feature["variants"])} | {accepted} | '
                     f'{len(feature["variants"]) - accepted} |')
    lines += ['', '## 限制', ''] + [f'- {item}' for item in report['limitations']]
    return '\n'.join(lines)


def _select(variants: list[sv.FunctionVariant], name: str | None, label: str) -> sv.FunctionVariant:
    if name is not None:
        for variant in variants:
            if variant.name == name:
                return variant
        raise ValueError(f'{label}: 找不到变体 {name}')
    if len(variants) != 1:
        raise ValueError(f'{label}: 文件含 {len(variants)} 个变体，请指定名称')
    return variants[0]


def _pair_report(reference: sv.FunctionVariant, candidate: sv.FunctionVariant, verdict: sv.Verdict) -> dict:
    return {
        'kind': 'vpf.semantic-comparison',
        'schemaVersion': 1,
        'proofLevel': verdict.proof_level,
        'emittedManifests': 0,
        'reference': sv.variant_to_json(reference),
        'candidate': sv.variant_to_json(candidate),
        'verdict': {
            'status': verdict.status,
            'reason': verdict.reason,
            'exact': verdict.exact,
            'candidateForReview': verdict.candidate,
            'normalizedDigest': verdict.normalized_digest,
            'registerMapping': [{'reference': left, 'candidate': right}
                                for left, right in verdict.register_mapping],
            'firstDifference': verdict.first_difference,
        },
    }


def _pair_markdown(report: dict) -> str:
    verdict = report['verdict']
    lines = [
        '# VPF 变体比较',
        '',
        f'- reference：`{report["reference"]["name"]}`',
        f'- candidate：`{report["candidate"]["name"]}`',
        f'- 证明等级：`{report["proofLevel"]}`',
        f'- 结果：`{verdict["status"]}` / `{verdict["reason"]}`',
        f'- exact：{verdict["exact"]}；人工复核候选：{verdict["candidateForReview"]}',
        f'- 生成的可安装 Manifest：{report["emittedManifests"]}',
    ]
    if verdict['registerMapping']:
        lines += ['', '| reference register | candidate register |', '| --- | --- |']
        for item in verdict['registerMapping']:
            lines.append(f'| `{item["reference"]}` | `{item["candidate"]}` |')
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', action='store_true', help='汇总仓库内 Manifest 函数的反汇编覆盖')
    parser.add_argument('--limit', type=int, help='限制分析的函数数量（测试/快速报告）')
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--reference-name')
    parser.add_argument('--candidate-name')
    parser.add_argument('--json', type=Path, dest='json_output')
    parser.add_argument('--markdown', type=Path, dest='markdown_output')
    parser.add_argument('--fail-on-rejected', action='store_true')
    args = parser.parse_args()

    if args.manifest:
        report = _manifest_report(generate_manifest.build_all(), args.limit)
        _write_json(args.json_output, report)
        _write_markdown(args.markdown_output, _manifest_markdown(report))
        print(f'VPF semantic report: {report["variantCount"]} variants, {report["disassembled"]} disassembled, '
              f'{report["disassemblyRejected"]} rejected, 0 emitted manifests')
        return 0

    if not args.reference or not args.candidate:
        parser.error('需要 --manifest 或同时提供 --reference/--candidate')
    reference = _select(sv.load_variants(args.reference), args.reference_name, 'reference')
    candidate = _select(sv.load_variants(args.candidate), args.candidate_name, 'candidate')
    verdict = sv.compare_variants(reference, candidate)
    report = _pair_report(reference, candidate, verdict)
    _write_json(args.json_output, report)
    _write_markdown(args.markdown_output, _pair_markdown(report))
    print(f'VPF semantic comparison: {verdict.status} / {verdict.reason}')
    return 1 if args.fail_on_rejected and verdict.status == sv.REJECTED else 0


if __name__ == '__main__':
    raise SystemExit(main())
