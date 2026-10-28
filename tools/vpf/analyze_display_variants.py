"""DVPF：显示补丁有界语义/立即数变体审计；不生成可安装 Manifest。"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/vpf'))

import display_semantics  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', type=Path, dest='json_output')
    parser.add_argument('--markdown', type=Path, dest='markdown_output')
    parser.add_argument('--fail-on-rejected', action='store_true')
    args = parser.parse_args()
    report = display_semantics.build_report()
    display_semantics.write_report(report, args.json_output, args.markdown_output)
    print(f'DVPF display semantic report: {report["siteCount"]} sites, '
          f'{report["exactValidated"]} exact, {len(report["dequantizationCandidates"])} immediate candidates, '
          f'{len(report["rejected"])} rejected, {report["emittedManifests"]} emitted manifests')
    if args.fail_on_rejected and (report['rejected'] or report['emittedManifests']):
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
