"""DVPF：只读采集挂载卷/生成 fixtures 的 OpenGL 参考页与动态适配证据。

用法：
  python3 tools/vpf/build_gl_evidence.py --system-root "/path/to/system-root" \
      --out-dir build/gl-sequoia --verify --emit
  python3 tools/vpf/build_gl_evidence.py --fixtures --check
  python3 tools/vpf/build_gl_evidence.py --fixtures --write

`--system-root` 模式不写系统卷；只输出报告和 JSON 证据表。`--fixtures` 模式从
仓库内已审查夹具确定性生成生产用 `NootedRed/VPF/GLManifestData.hpp`。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/vpf'))
sys.path.insert(0, str(ROOT / 'tools/dvpf'))

import gl_evidence  # noqa: E402

OUTPUT = ROOT / 'NootedRed/VPF/GLManifestData.hpp'


def _write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + '\n')


def _fixtures(args: argparse.Namespace) -> int:
    evidence = gl_evidence.build_fixture_evidence()
    text = gl_evidence.emit_cpp(evidence)
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text() != text:
            print(f'GLManifestData 生成头已陈旧或被手改: {OUTPUT}', file=sys.stderr)
            return 1
    if args.write:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(text)
    print(f'DVPF GLManifestData: {len(evidence)} 条参考页证据 -> {OUTPUT}')
    return 0


def _system(args: argparse.Namespace) -> int:
    if args.out_dir is None:
        print('--system-root 需要 --out-dir', file=sys.stderr)
        return 1
    try:
        result = gl_evidence.inspect_system_root(args.system_root, args.darwin)
    except (OSError, ValueError) as error:
        print(f'GL 证据采集失败: {error}', file=sys.stderr)
        return 1
    out = args.out_dir
    if args.emit:
        _write_json(out / 'gl-proof.json', gl_evidence.proof_document(result))
        _write_json(out / 'gl-variants.json', gl_evidence.variants_document(result))
        _write_json(out / 'gl-manifest-data.json', gl_evidence.manifest_data_document(result))
    source = result['source']
    print(f"DVPF GL evidence: {source['productVersion']} {source['buildVersion']} "
          f"Darwin{source['darwin']} SHA256={source['fileSHA256'][:16]}...")
    for entry in result['evidence']:
        print(f"  {entry['feature']}: {entry['status']} candidates={entry['candidateCount']} "
              f"pageMatch={entry.get('referencePageMatched', False)} "
              f"runtimeCompatible={entry.get('runtimeCompatible', False)}")
    if not result['allReferenceBlocksFound']:
        print('未找到全部参考块；报告已记录限制，不能宣称该版本已适配。', file=sys.stderr)
        return 2 if args.verify else 0
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--system-root', type=Path)
    parser.add_argument('--out-dir', type=Path)
    parser.add_argument('--verify', action='store_true', help='参考块缺失/不兼容时返回非零')
    parser.add_argument('--emit', action='store_true', help='写出 gl-proof/gl-variants/gl-manifest-data')
    parser.add_argument('--darwin', type=int, help='覆盖从 SystemVersion 推导的 Darwin 主版本')
    parser.add_argument('--fixtures', action='store_true', help='从仓库夹具生成生产证据表')
    parser.add_argument('--check', action='store_true', help='确认 GLManifestData.hpp 与夹具一致')
    parser.add_argument('--write', action='store_true', help='写出 NootedRed/VPF/GLManifestData.hpp')
    args = parser.parse_args()
    if args.system_root:
        return _system(args)
    return _fixtures(args)


if __name__ == '__main__':
    raise SystemExit(main())
