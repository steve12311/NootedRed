"""逐项比较两份 VPF 计划 JSON；输出第一个差异的路径。

计划键为 feature/os/deviceID/state；比较覆盖映像、站点地址/大小、
全部原始与替换字节以及读取计数（可用 --ignore-reads 排除计数）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def plan_key(plan: dict) -> str:
    return f"{plan['feature']}/{plan['os']}/{plan['deviceID']:04X}/{plan['state']}"


def _short(value) -> str:
    text = repr(value)
    return text if len(text) <= 96 else text[:93] + '...'


def _diff(path: str, old, new, errors: list[str]) -> None:
    if type(old) is not type(new):
        errors.append(f'{path}: 类型不同 {type(old).__name__} != {type(new).__name__}')
        return
    if isinstance(old, dict):
        for key in sorted(set(old) | set(new)):
            if key not in old:
                errors.append(f'{path}.{key}: 新计划新增')
            elif key not in new:
                errors.append(f'{path}.{key}: 新计划缺失')
            else:
                _diff(f'{path}.{key}', old[key], new[key], errors)
        return
    if isinstance(old, list):
        if len(old) != len(new):
            errors.append(f'{path}: 长度 {len(old)} != {len(new)}')
        for index, (left, right) in enumerate(zip(old, new)):
            _diff(f'{path}[{index}]', left, right, errors)
        return
    if old != new:
        errors.append(f'{path}: {_short(old)} != {_short(new)}')


def compare(old: dict, new: dict, *, ignore_reads: bool = False) -> list[str]:
    errors: list[str] = []
    if old.get('schemaVersion') != new.get('schemaVersion'):
        errors.append('schemaVersion 不同')
    if old.get('kind') != new.get('kind'):
        errors.append('kind 不同')
    old_plans = {plan_key(plan): plan for plan in old.get('plans', [])}
    new_plans = {plan_key(plan): plan for plan in new.get('plans', [])}
    if len(old_plans) != len(old.get('plans', [])):
        errors.append('旧计划存在重复键')
    if len(new_plans) != len(new.get('plans', [])):
        errors.append('新计划存在重复键')
    for key in sorted(set(old_plans) | set(new_plans)):
        if key not in old_plans:
            errors.append(f'计划 {key}: 仅新计划存在')
            continue
        if key not in new_plans:
            errors.append(f'计划 {key}: 仅旧计划存在')
            continue
        left, right = dict(old_plans[key]), dict(new_plans[key])
        if ignore_reads:
            left.pop('reads', None)
            right.pop('reads', None)
        _diff(f'计划 {key}', left, right, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('old', type=Path)
    parser.add_argument('new', type=Path)
    parser.add_argument('--ignore-reads', action='store_true', help='忽略读取计数差异')
    parser.add_argument('--max-errors', type=int, default=20)
    args = parser.parse_args()
    old = json.loads(args.old.read_text())
    new = json.loads(args.new.read_text())
    errors = compare(old, new, ignore_reads=args.ignore_reads)
    if errors:
        for error in errors[: args.max_errors]:
            print(f'DIFF: {error}', file=sys.stderr)
        if len(errors) > args.max_errors:
            print(f'... 另有 {len(errors) - args.max_errors} 项差异', file=sys.stderr)
        return 1
    print(f'PASS plan compare: {len(old.get("plans", []))} plans 逐项一致')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
