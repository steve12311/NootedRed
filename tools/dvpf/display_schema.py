"""DVPF 离线显示清单的稳定常量；不访问系统缓存，不生成可安装行为。"""
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

DOMAINS = (
    'OpenGLValidatedPage',
    'MetalSharedCache',
    'KernelGraphics',
    'KernelFramebuffer',
    'GPUObservation',
)

FEATURES = (
    'OpenGLHwInfo',
    'OpenGLDualQuad',
    'MetalSrdShared',
    'MetalLegacyBlend',
    'MetalComputeScratch',
    'MetalPipelineConstructor',
    'MetalSubmit',
    'MetalSubmitGuard',
    'KernelGFX9Capabilities',
    'KernelDCNScanout',
    'KernelFramebuffer',
    'HDRResearch',
)

GROUPS = (
    'GLHwInfo',
    'GLDualQuad',
    'MetalRenderCore',
    'MetalScratch',
    'MetalSubmitSafety',
    'KernelGFX9',
    'KernelScanout',
    'HDRResearch',
)

EVIDENCE = (
    'ExactKnownPage',
    'FullPageLocalBlock',
    'FullFunctionWithRefs',
    'FullFunctionWithScopedSections',
    'ExactStaticPayload',
    'RuntimeTrampoline',
    'KernelSymbolAndABI',
    'DescriptiveOnly',
)

GROUP_MEMBERS = {
    'GLHwInfo': ('OpenGLHwInfo',),
    'GLDualQuad': ('OpenGLDualQuad',),
    'MetalRenderCore': ('MetalSrdShared', 'MetalLegacyBlend', 'MetalPipelineConstructor'),
    'MetalScratch': ('MetalComputeScratch',),
    'MetalSubmitSafety': ('MetalSubmitGuard', 'MetalSubmit'),
    'KernelGFX9': ('KernelGFX9Capabilities',),
    'KernelScanout': ('KernelDCNScanout', 'KernelFramebuffer'),
    'HDRResearch': ('HDRResearch',),
}

FEATURE_ORDER = {name: index for index, name in enumerate(FEATURES)}
GROUP_ORDER = {name: index for index, name in enumerate(GROUPS)}

# 显示路径总开关；不得重新激活旧的 ImmediateSync/SrdShared/LegacyBlend/ComputeScratch 参数。
DISPLAY_FIX_ARG = '-NRedDisplayFix'
INACTIVE_ARGS = ('-NRedImmediateSync', '-NRedSrdShared', '-NRedLegacyBlend', '-NRedComputeScratch')

# 合成和用户态测试不能证明当前版本通过实机验收。
HARDWARE_BASELINE = {
    'status': '当前版本尚未完成实机启动和图形工作负载验收。',
}


def os_mask(names: list[str] | tuple[str, ...]) -> int:
    mask = 0
    for name in names:
        if name not in OS_BITS:
            raise ValueError(f'未知 Darwin 掩码: {name}')
        mask |= OS_BITS[name]
    return mask


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n'


def digest(value) -> str:
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def format_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
