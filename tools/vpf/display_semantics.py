"""DVPF：显示补丁的离线有界语义审计；默认不生成可安装 Manifest。

约束：
- 站点必须携带与大小完全一致的原始/替换字节；不接受 mask、通配或短签名；
- rel32/引用字段必须由调用方显式声明，比较时不得当作可自由变化的语义字段；
- 立即数变体必须显式列出偏移/长度，且引用字段与其余字节保持逐字节一致；
- 报告只用于人工复核，`emittedManifests` 恒为 0。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/vpf'))
sys.path.insert(0, str(ROOT / 'tools/dvpf'))

PROOF_LEVEL = 'ExactBytesWithTypedReferences'
MAX_IMMEDIATE_BYTES = 8


@dataclass(frozen=True)
class Reference:
    offset: int
    size: int
    kind: str
    target: int
    image: int = 0
    indirect: int = 0


@dataclass(frozen=True)
class Site:
    key: str
    feature: str
    domain: str
    os: tuple[str, ...]
    deviceID: int
    group: str
    evidence: str
    size: int
    original: bytes
    replacement: bytes
    references: tuple[Reference, ...]
    dynamic_offset: int = 0
    dynamic_size: int = 0
    installable: bool = True

    def __post_init__(self):
        if not self.key or self.size <= 0 or len(self.original) != self.size or len(self.replacement) != self.size:
            raise ValueError(f'{self.key}: 字节长度必须与站点大小一致')
        if not 0 <= self.dynamic_offset <= self.size or self.dynamic_offset + self.dynamic_size > self.size:
            raise ValueError(f'{self.key}: 动态字段越界')
        previous = -1
        for reference in self.references:
            if reference.size <= 0 or reference.offset < 0 or reference.offset + reference.size > self.size:
                raise ValueError(f'{self.key}: 引用越界')
            if reference.offset < previous:
                raise ValueError(f'{self.key}: 引用重叠或未排序')
            previous = reference.offset + reference.size


def _reference_mask(site: Site) -> set[int]:
    result: set[int] = set()
    for reference in site.references:
        result.update(range(reference.offset, reference.offset + reference.size))
    return result


def _site_refs(original: list[dict], patched: list[dict], start: int, size: int) -> tuple[Reference, ...]:
    ranges = []
    for item in list(original) + list(patched):
        relative = item['offset'] - start
        if relative < 0 or relative + 4 > size:
            continue
        ranges.append((relative, relative + 4, item.get('rva', 0), item.get('image', 0), item.get('indirect', 0)))
    ranges.sort()
    merged = []
    for begin, end, target, image, indirect in ranges:
        if merged and begin <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end), merged[-1][2], merged[-1][3], merged[-1][4])
        else:
            merged.append((begin, end, target, image, indirect))
    return tuple(Reference(begin, end - begin, 'rel32', target, image, indirect)
                 for begin, end, target, image, indirect in merged)


def load_sites() -> list[Site]:
    import display_data
    import generate_manifest

    features = display_data.build_all()
    result: list[Site] = []
    for feature in features:
        if feature['domain'] == 'OpenGLValidatedPage':
            site = feature['sites'][0]
            refs = _site_refs(feature['refs']['original'], feature['refs']['patched'],
                              site['instructionOffset'], site['size'])
            result.append(
                Site(
                    key=feature['key'],
                    feature=feature['feature'],
                    domain=feature['domain'],
                    os=tuple(feature['os']),
                    deviceID=feature['deviceID'],
                    group=feature['group'],
                    evidence=feature['evidence'],
                    size=site['size'],
                    original=site['original'],
                    replacement=site['replacement'],
                    references=refs,
                    dynamic_offset=site['dynamicOffset'],
                    dynamic_size=site['dynamicSize'],
                    installable=feature['installable'],
                )
            )
        elif feature['feature'] == 'MetalSubmitGuard':
            site = feature['sites'][0]
            result.append(
                Site(
                    key=feature['key'],
                    feature=feature['feature'],
                    domain=feature['domain'],
                    os=tuple(feature['os']),
                    deviceID=feature['deviceID'],
                    group=feature['group'],
                    evidence=feature['evidence'],
                    size=site['size'],
                    original=site['original'],
                    replacement=site['replacement'],
                    references=(),
                    dynamic_offset=site['dynamicOffset'],
                    dynamic_size=site['dynamicSize'],
                    installable=feature['installable'],
                )
            )
    for feature in generate_manifest.build_all():
        if feature['feature'] not in ('DisplaySRD', 'DisplayLegacyBlend', 'DisplayComputeScratch',
                                      'DisplayPipelineConstructor', 'DisplaySubmit'):
            continue
        for index, patch in enumerate(feature['patches']):
            if patch['function'] is None:
                refs = ()
            else:
                function = feature['functions'][patch['function']]
                refs = _site_refs(function['originalRefs'], function['patchedRefs'],
                                  patch['instructionOffset'], patch['size'])
            result.append(
                Site(
                    key=f'{feature["key"]}#{index}',
                    feature=feature['feature'],
                    domain='MetalSharedCache',
                    os=tuple(feature['os']),
                    deviceID=feature['deviceID'],
                    group='Metal',
                    evidence=feature['evidence'],
                    size=patch['size'],
                    original=patch['_original'],
                    replacement=patch['_patched'],
                    references=refs,
                    installable=True,
                )
            )
    return result


def diff_runs(site: Site) -> list[dict]:
    mask = _reference_mask(site)
    runs = []
    current = None
    for index in range(site.size):
        if index in mask or site.original[index] == site.replacement[index]:
            if current is not None:
                runs.append(current)
                current = None
            continue
        if current is None:
            current = {'offset': index, 'size': 1}
        else:
            current['size'] += 1
    if current is not None:
        runs.append(current)
    for run in runs:
        start = run['offset']
        end = start + run['size']
        run['original'] = site.original[start:end].hex()
        run['replacement'] = site.replacement[start:end].hex()
    return runs


def immediate_candidate(site: Site) -> dict | None:
    if site.size < 8 or site.evidence in ('DescriptiveOnly', 'KernelSymbolAndABI', 'ExactStaticPayload',
                                         'RuntimeTrampoline'):
        return None
    runs = diff_runs(site)
    if not runs or any(run['size'] > MAX_IMMEDIATE_BYTES for run in runs):
        return None
    return {'immediates': runs, 'bounded': True, 'requiresReview': True}


def validate_candidate(site: Site, candidate: dict) -> tuple[bool, str]:
    """候选必须给出完整字节；只允许显式立即数字段变化。"""
    forbidden = {'mask', 'wildcard', 'signature', 'signatureLength', 'pattern'}
    if forbidden & set(candidate):
        return False, 'wildcard_or_short_signature'
    if candidate.get('feature') != site.feature or candidate.get('deviceID') != site.deviceID:
        return False, 'identity_mismatch'
    try:
        original = bytes.fromhex(candidate['original'])
        replacement = bytes.fromhex(candidate['replacement'])
    except (KeyError, TypeError, ValueError):
        return False, 'missing_full_bytes'
    if len(original) != site.size or len(replacement) != site.size:
        return False, 'length_mismatch'
    if original != site.original:
        return False, 'original_mismatch'
    immediates = candidate.get('immediates')
    if immediates is None:
        return (True, 'exact') if replacement == site.replacement else (False, 'replacement_mismatch')
    if not isinstance(immediates, list) or not immediates:
        return False, 'missing_immediates'
    allowed: set[int] = set()
    for item in immediates:
        if not isinstance(item, dict):
            return False, 'bad_immediate'
        offset, size = item.get('offset'), item.get('size')
        if not isinstance(offset, int) or not isinstance(size, int) or size <= 0 or offset < 0 \
                or offset + size > site.size:
            return False, 'immediate_range'
        allowed.update(range(offset, offset + size))
    references = _reference_mask(site)
    if allowed & references:
        return False, 'immediate_overlaps_reference'
    for index in range(site.size):
        if index in allowed or index in references:
            continue
        if replacement[index] != site.replacement[index]:
            return False, 'unproven_byte_change'
    for index in references:
        if replacement[index] != site.replacement[index]:
            return False, 'reference_changed'
    return True, 'bounded_immediate'


def build_report(sites: list[Site] | None = None) -> dict:
    sites = sites or load_sites()
    records = []
    candidates = []
    rejected = []
    for site in sites:
        runs = diff_runs(site)
        candidate = immediate_candidate(site)
        record = {
            'key': site.key,
            'feature': site.feature,
            'domain': site.domain,
            'os': list(site.os),
            'deviceID': site.deviceID,
            'group': site.group,
            'evidence': site.evidence,
            'size': site.size,
            'installable': site.installable,
            'references': [{'offset': ref.offset, 'size': ref.size, 'kind': ref.kind, 'target': ref.target}
                           for ref in site.references],
            'diffRuns': runs,
            'smallDiffRuns': sum(1 for run in runs if run['size'] <= MAX_IMMEDIATE_BYTES),
            'staticPayload': site.size < 8 and site.evidence in ('ExactStaticPayload', 'RuntimeTrampoline'),
            'immediateCandidate': candidate is not None,
        }
        exact = {
            'feature': site.feature,
            'deviceID': site.deviceID,
            'original': site.original.hex(),
            'replacement': site.replacement.hex(),
        }
        valid, reason = validate_candidate(site, exact)
        if not valid:
            rejected.append({'key': site.key, 'reason': reason})
        records.append(record)
        if candidate is not None:
            candidates.append({'key': site.key, 'feature': site.feature, 'domain': site.domain,
                               'immediates': candidate['immediates'], 'bounded': candidate['bounded'],
                               'requiresReview': candidate['requiresReview']})
    return {
        'kind': 'vpf.display-semantic-report',
        'schemaVersion': 1,
        'proofLevel': PROOF_LEVEL,
        'emittedManifests': 0,
        'siteCount': len(records),
        'domains': {domain: sum(1 for site in sites if site.domain == domain)
                    for domain in sorted({site.domain for site in sites})},
        'exactValidated': len(records) - len(rejected),
        'rejected': rejected,
        'dequantizationCandidates': candidates,
        'sites': records,
        'limitations': [
            '报告只比较当前生成站点中的完整字节和显式引用/立即数字段；不证明 GPU 执行结果。',
            '立即数候选仍需人工反汇编、寄存器/CFG 复核和实机证据，不能直接进入 Manifest。',
            'Metal 函数引用在本报告中只核对生成站点的类型化字段，不重新执行运行时唯一性扫描。',
            'emittedManifests 恒为 0；任何候选都不改变运行时准入。',
        ],
    }


def markdown(report: dict) -> str:
    lines = [
        '# DVPF 显示语义变体报告',
        '',
        f'- 证明等级：`{report["proofLevel"]}`',
        f'- 站点：{report["siteCount"]}；通过完整字节校验：{report["exactValidated"]}',
        f'- 立即数候选：{len(report["dequantizationCandidates"])}；拒绝：{len(report["rejected"])}',
        f'- 生成的可安装 Manifest：{report["emittedManifests"]}',
        '',
        '| 站点 | 域 | 大小 | 差异段 | 立即数候选 |',
        '| --- | --- | ---: | ---: | --- |',
    ]
    for site in report['sites']:
        lines.append(f'| `{site["key"]}` | {site["domain"]} | {site["size"]} | {len(site["diffRuns"])} | '
                     f'{"是" if site["immediateCandidate"] else "否"} |')
    lines += ['', '## 限制', ''] + [f'- {item}' for item in report['limitations']]
    return '\n'.join(lines) + '\n'


def write_report(report: dict, json_path: Path | None, markdown_path: Path | None) -> None:
    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + '\n')
    if markdown_path is not None:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(markdown(report))
