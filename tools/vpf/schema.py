"""VPF 离线数据的稳定序列化与版本常量。"""
from __future__ import annotations

import hashlib
import json

SCHEMA_VERSION = 1

# 与 NootedRed/VPF/PatchManifest.hpp 的 OsDarwin* 位一一对应。
OS_BITS = {
    'Darwin23': 1,
    'Darwin24': 2,
    'Darwin25': 4,
}

FEATURE_IDS = (
    'DisplaySRD',
    'DisplayLegacyBlend',
    'DisplayComputeScratch',
    'DisplaySubmitGuard',
    'DisplayPipelineConstructor',
    'DisplaySubmit',
    'Vcn2Decode',
    'Vcn2Encode',
    'AppleGVASelection',
    'FairPlayNativeDecodeSubset',
    'Vcn2HEVCMain10',
)

EVIDENCE_KINDS = (
    'ExactValidatedBuild',
    'RelocationAwareFullFunction',
    'OfflineProvenSemanticVariant',
)

PLAN_STATES = ('Unknown', 'AllOriginal', 'AllPatched', 'Mixed')

USER_LIMIT = 0x0000800000000000


def os_mask(names: list[str] | tuple[str, ...]) -> int:
    mask = 0
    for name in names:
        if name not in OS_BITS:
            raise ValueError(f'未知 Darwin 掩码: {name}')
        mask |= OS_BITS[name]
    return mask


def canonical_json(value) -> str:
    """排序键、固定分隔符；同一输入必须逐字节相同。"""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n'


def digest(value) -> str:
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def hex64(value: int) -> str:
    if not isinstance(value, int) or value < 0 or value >= 1 << 64:
        raise ValueError(f'非法 64 位值: {value!r}')
    return f'0x{value:016X}'


def format_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
