# Vega 核显 / Tahoe 动态缓存匹配 Revision 3

本候选由本地工作区修改，基于 NootedRed 0.9.0；保留原作者版权与许可证。
沿用原作者接受的 Vega 核显 PCI 分类：`1002:15D8 / 15DD / 15E7 / 1636 / 1638 / 164C`。
未增加 Vega 独显支持，7430U 的真实 Chromium / Discord 效果仍待测试人员验证。

## UUID 与兼容判定

运行时读取进程实际映射的 x86_64h 缓存 UUID。参考 UUID
`31374756-89B4-34E8-A34F-56C5D57216A1` 保留原快速路径；其他 UUID 自动进入动态核验。

动态路径按缓存 image table 中的完整驱动路径找到 `AMDRadeonX5000MTLDriver`，解析 Mach-O
可执行 `__text` 与只读/常量段，先核验映像的参考 RVA 位置；位置不符时，在限定范围内唯一定位原生配置函数、scratch 原生函数、
完整混合 pipeline 构造函数、初始化函数和独立覆盖入口。初始化的三个相对引用允许改变，
但配置调用必须落到已核实的完整原生函数，调试目标必须在驱动代码段且内容匹配，
浮点常量必须在驱动的 `__const` 段并保持 3.0f。

只有完整内容与目标核验通过，才重新编码 RIP 引用、原生调用和覆盖跳转；写入范围仍为原先
的同步、SRD 与混合补丁。完整 pipeline 构造函数用于上下文匹配，实际只修改两字节分支。
原生 scratch 函数必须未被其他实验修改，动态路径不应用 scratch 拓扑实验。

这使内容兼容、缓存打包位置不同的系统共用同一测试包。驱动实现或未支持的指令布局改变时
仍拒绝应用，需要新的核实资料；没有自动适配任意驱动版本。

解析有读取字节数、次数、候选数、映像条目数和段大小上限，未知位置的重复匹配拒绝应用。
跨页补丁逐页检查 RX、执行 COW 写入，并在失败时恢复原始字节与保护。实际缓存测试使用
3,959 次读取，Python 回调测试约 0.011 秒；它不代表内核实际开机或应用启动开销。

## 测试方法

1. 保留可启动的 EFI 备份，用本包的 `NootedRed.kext` 替换测试 EFI 中的版本。
2. 先以 `-NRedLegacyBlend` 重启并测试渲染修复；它自动包含 ImmediateSync 与 SrdShared。
   单独测试 scratch 时，改用 `-NRedComputeScratch` 重启。scratch 为六个受支持的 Vega APU PCI ID
   生成了逐设备载荷，只修正匹配该 APU 的 scratch 分配；Vega 独显仍使用原生 SE 数。
   scratch 仍要求参考缓存 UUID，其他缓存会拒绝应用。`-NRedTransfer1x1` 沿用重定向兼容入口，
   VCN 参数沿用其原有设备和版本限制。
3. 只读确认加载版本与诊断字段：

   ```sh
   kmutil showloaded | rg -i 'NootedRed|Lilu'
   ioreg -l -w0 -r -n IGPU | rg 'NRed(ImmediateSync|SrdShared|LegacyBlend)'
   sw_vers
   uname -r
   ```

4. 应看到 `NRedImmediateSyncRevision=3`、`Requested=1`、`NRedLegacyBlendEnabled=1`。
   参考缓存以 `NRedLegacyBlendApplied` / `WindowServerApplied` 判断是否写入完成；
   其他缓存还应看到 `NRedImmediateSyncDynamicMatched=1`、`DynamicApplied=1`。
5. 开启应用硬件加速，复测 Chromium 地址栏连续输入与建议列表、Discord server settings。
   回传第 3 步输出及应用版本、花屏/冻结结果即可；如再次故障，记录时间并重启恢复桌面。

`Enabled` 只说明注册了 hook，`DynamicMatched` 只说明生成了兼容计划；`Applied` 及
`WindowServerApplied` 表示实际写入并恢复 RX，也不能代替应用运行结果。
UUID 四个字段是原始字节每四字节的小端 UInt32，按该顺序解码即可得到实际缓存 UUID。

## 拒绝与回退

- `Rejected=1/2/3/4`：符号缺失 / hook 失败 / PCI 不支持 / 非 Darwin 25。
- `DynamicRejected=1`、`D04=1`：缓存元数据、驱动内容、引用目标、唯一匹配或解析上限不满足；
  未知缓存传入 scratch 参数也会拒绝，因为 scratch 位置尚未动态重定位。
- `D06=1` / `D08=1`：原始指令不匹配 / 进程残留另一补丁模式。
- `D10`–`D14`：区域、保护、写入、恢复或回滚失败。

回退时恢复 EFI 中先前的 kext、移除测试参数并重启。包内附完整修改源码和验证记录；
没有安装到本机 EFI，也没有修改系统缓存文件。

## 验证边界

自动回归覆盖原生指令、全部受支持 APU 的逐 ID scratch 载荷及 Vega 独显负向路径、完整 16 位 PCI 空间、动态解析与重定位、错误目标、损坏元数据、
重复匹配、读取上限、六页部分写入回滚、ASan/UBSan，以及实际缓存的 UUID/ASLR 模拟。
真实的另一 macOS 缓存版本、目标机器启动、Chromium/Discord 修复效果和长期负载仍需实机验收。

缓存与映像字段参考 [Apple dyld 缓存格式](https://github.com/apple-oss-distributions/dyld/blob/main/include/mach-o/dyld_cache_format.h)。
