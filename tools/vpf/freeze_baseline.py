"""冻结 VPF 基线：提交、生成头哈希与计划摘要。

摘要中的每个计划 digest 覆盖映像、站点偏移、全部原始/替换字节与读取计数；
完整的计划 JSON 另存到 build/vpf/baseline-plans.json，供人工审查和 compare_plan。
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_manifest
import plan_dump
import schema

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / 'tools/vpf/fixtures/baseline.json'
FULL = ROOT / 'build/vpf/baseline-plans.json'

TRACKED = (
    'NootedRed/UserCacheResolver.hpp',
    'NootedRed/UserSharedCache.hpp',
    'NootedRed/UserVideoDecodeResolver.hpp',
    'NootedRed/UserSurfaceSyncResolver.hpp',
    'NootedRed/VPF/PatchManifest.hpp',
    'NootedRed/VPF/PatchManifestData.hpp',
    'NootedRed/VPF/VerifiedPatchPlan.hpp',
    'NootedRed/VPF/ResolverAdapters.hpp',
    'tools/cache_resolver_data.py',
    'tools/vpf/cppdata.py',
    'tools/vpf/schema.py',
    'tools/vpf/generate_manifest.py',
    'tools/vpf/plan_probe.cpp',
    'tools/vpf/plan_dump.py',
    'tools/vpf/compare_plan.py',
    'tools/vpf/freeze_baseline.py',
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _key(plan: dict) -> str:
    return f"{plan['feature']}/{plan['os']}/{plan['deviceID']:04X}/{plan['state']}"


def freeze(output: Path, full_output: Path, *, sanitize: bool, commit: str | None = None) -> dict:
    document = plan_dump.dump_plans(sanitize=sanitize)
    commit = commit or subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    baseline = {
        'schemaVersion': schema.SCHEMA_VERSION,
        'kind': 'vpf.baseline',
        'commit': commit,
        'generatedHeaders': {
            f'NootedRed/{name}': _hash(ROOT / 'NootedRed' / name) for name in generate_manifest.HEADERS
        },
        'tools': {name: _hash(ROOT / name) for name in TRACKED},
        'planCount': len(document['plans']),
        'plans': [
            {
                'key': _key(plan),
                'digest': schema.digest(plan),
                'reads': plan['reads'],
                'sites': len(plan['sites']),
                'images': len(plan['images']),
            }
            for plan in document['plans']
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(schema.canonical_json(baseline))
    if full_output:
        full_output.parent.mkdir(parents=True, exist_ok=True)
        full_output.write_text(schema.canonical_json(document))
    return baseline


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=BASELINE)
    parser.add_argument('--full-output', type=Path, default=FULL)
    parser.add_argument('--no-full', action='store_true')
    parser.add_argument('--sanitize', action='store_true')
    parser.add_argument('--commit', help='基线运行时提交；默认当前 HEAD')
    args = parser.parse_args()
    baseline = freeze(args.output, None if args.no_full else args.full_output, sanitize=args.sanitize,
                      commit=args.commit)
    print(f"PASS baseline freeze: {baseline['planCount']} plans at {baseline['commit'][:12]} -> {args.output}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
