"""校验 VPF Manifest 的确定性、预算与补丁证据；不访问系统缓存。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_manifest
import schema


def check() -> list[dict]:
    first = generate_manifest.build_all()
    second = generate_manifest.build_all()
    first_text = generate_manifest.emit_cpp(first)
    second_text = generate_manifest.emit_cpp(second)
    if first_text != second_text:
        raise ValueError('Manifest 两次生成不一致')
    if not generate_manifest.OUTPUT.exists():
        raise ValueError(f'缺少生成头: {generate_manifest.OUTPUT}')
    if generate_manifest.OUTPUT.read_text() != first_text:
        raise ValueError(f'生成头已陈旧或被手改: {generate_manifest.OUTPUT}')
    keys = [feature['key'] for feature in first]
    if len(keys) != len(set(keys)):
        raise ValueError('功能键重复')
    return first


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', type=Path, help='写出规范化 Manifest JSON')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args()
    try:
        features = check()
    except (ValueError, KeyError, TypeError) as error:
        print(f'FAIL: {error}', file=sys.stderr)
        return 1
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            schema.canonical_json(
                {
                    'schemaVersion': schema.SCHEMA_VERSION,
                    'kind': 'vpf.manifest',
                    'features': [generate_manifest._strip(feature) for feature in features],
                }
            )
        )
    if not args.quiet:
        print(
            f'PASS VPF Manifest: {len(features)} 个功能实例，'
            f'{sum(len(feature["patches"]) for feature in features)} 个补丁站点'
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
