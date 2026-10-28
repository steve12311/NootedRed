# 内核显示 / DCN 只读证据

> 状态：代码、主机探针和只读属性已实现；**未在 14/15/26 实机启动中采集最终数据**。
> 本文不声称 HDR、Flip、GFXOFF 或 Compute 已修复。

## 目的

X5000 / X6000FB / AMDGFX9DCNDisplay 提供可追溯的只读证据。该功能不改变内核补丁、
显示寄存器、Flip 等待或电源管理。

## 采集点

| 位置 | 证据 | 写入时机 |
| --- | --- | --- |
| `NRed::hwLateInit` | GFX9 deviceID、PCI/dev/enum revision、attributes、fbOffset | 初始化一次 |
| `X5000::processKext` | X5000 kext Mach-O UUID/source version/size、计算提交虚表是否匹配 | kext 加载一次 |
| `X6000FB::processKext` | X6000FB kext Mach-O UUID/source version/size、Darwin、Renoir/Picasso/RenoirE flags | kext 加载一次 |
| `AMDGFX9DCNDisplay::getDisplayInfo` | 每 fb 的 bpp/bpc、hw pixel format、swizzle、rotation | 模式变化 |
| `writeFlipParameters` | 每 fb 的 surface format、pitch、rotation | 参数变化 |
| `setCurrentDisplayOffset` | 原有 `while (isFlipPending)` 的等待次数、最大/累计等待 | 每次 flips 后计数；长等待才发布 |
| `isFlipPending` | 保持原实现，不增加计数或超时 | 不变 |

热路径只做 `__atomic` 计数；`publish()` 只在初始化、模式变化或最大 Flip 等待增加且
达到 10 次（约 1 ms）时调用。属性发布使用固定的 32 位字段，不拼接任意地址字符串。

## 属性

- `NRedKernelDisplayEvidenceRevision`
- `NRedKernelGFX9DeviceID`、`NRedKernelGFX9Revisions`、`NRedKernelGFX9Attributes`、
  `NRedKernelGFX9FbOffsetLow/High`
- `NRedKernelKextValid/Error`、`NRedKernelX5000KextSize`、`NRedKernelX5000KextUUID0..3`、
  `NRedKernelX5000SourceVersionLow/High`、X6000FB 对应字段
- `NRedKernelComputeVTable`、`NRedKernelFramebufferDarwin`、`NRedKernelFramebufferFlags`
- `NRedKernelScanoutFormat0..3`、`NRedKernelScanoutSwizzle0..3`、`NRedKernelScanoutChanges`
- `NRedKernelFlipFormat0..3`、`NRedKernelFlipPitch0..3`、`NRedKernelFlipRotation0..3`
- `NRedKernelFlipWaitCount`、`NRedKernelFlipWaitMax`、`NRedKernelFlipWaitTotal`

这些字段只表示“代码看到了什么”，不表示 GPU 已正确执行。例如 `NRedKernelFlipWaitMax`
只能说明等待循环次数，不能单独归因 Fence 超时。

## 实机采集建议

```sh
ioreg -lw0 -r -n IGPU | rg 'NRedKernel'
log show --last boot --style compact --predicate 'process == "WindowServer" AND eventMessage CONTAINS[c] "timed out"'
log show --last boot --style compact --predicate 'eventMessage CONTAINS[c] "GPU Restart" OR eventMessage CONTAINS[c] "computeUQ" OR eventMessage CONTAINS[c] "VTMTSComputeFunction"'
```

采集时必须同时记录 boot UUID、macOS build、kext UUID、PCI ID、显示器/EDID 和显示模式；
不同启动记录不得混合。日志无结果不能证明没有 GPU 错误。

## 明确未做

- 未修改 `getDisplayInfo` 的格式选择或 `writeFlipParameters` 的返回语义；
- 未给 `isFlipPending()` 增加超时或跳过；
- 未启用 HDRIntegerFilter，未修改 DCN/DMCUB 寄存器；
- 未把 GFXOFF/Compute 现象归因于 SubmitGuard 或共享缓存协调器；
- 未在实机上验证属性发布频率和 IORegistry 开销。

## 主机验证

`python3 tools/test_kernel_display_evidence.py` 使用合成 Mach-O 和 NRed 属性桩验证：
UUID/source version 采集、无效 Mach-O 拒绝、scanout/flip 只在变化时返回 true、长 Flip 等待
阈值和属性总量。该测试不运行内核，也不替代实机。
