# Vega APU Metal 渲染与 Scratch 修复

## 修复内容

Revision 3 的同步、SRD 与混合修复匹配原作者 `NRed::processPatcher` 接受的全部 Vega 核显
PCI ID：`0x15D8`、`0x15DD`、`0x15E7`、`0x1636`、`0x1638`、`0x164C`，覆盖
Raven/Picasso、Renoir 与 Cezanne/Barcelo 系列。生成器直接读取该分类，避免额外维护 CPU 型号表。
Tahoe 的 Darwin 25.x 均可进入核验。已知 x86_64h dyld cache UUID 使用既有快速路径；
其他 UUID 按实际驱动路径、Mach-O 段和完整函数内容动态定位，再核验引用目标并生成重定位载荷。
参考指令数据来自 Darwin 25.6 / 用户报告的 macOS 26.7.1；实现、布局或指令不兼容时仍拒绝应用。
默认不开启用户态补丁；新增设备的真实桌面效果仍待测试人员验证。

- `X5000` 的 compute submit 虚表修复跳过 Itanium ABI 的两个头部槽位，并核对原提交函数地址。
- `UserSurfaceSync` 使用原生立即屏障路径，并为目标设备选择原生 shared SRD 表分配。
- LegacyBlend 修复 pipeline 构造中的 RB+ 能力分支：缺少能力报告时，仍计算
  `dualSource || !allowRBPlus`，正确设置 `CB_COLOR_CONTROL.DISABLE_DUAL_QUAD`。
- ComputeScratch 仅在目标设备的 scratch 分配中使用物理的一个 SE，避免虚拟 4 SE
  将 scratch 环从 256 waves 配置为 1024 waves；原生 kernel、设备报告的拓扑及 VCN 不变。
  生成器为原作者支持的六个 APU PCI ID 分别生成载荷，分配器只对匹配的 APU ID 跳过虚拟 SE；
  其他设备（包括 Vega 独显）保留原生 SE 乘法。scratch 地址仍限定参考缓存 UUID。

用户已在 Ryzen 7 5800H/Cezanne 上确认 LegacyBlend 解决微信和 Chrome 的花屏、碎屏问题。
原生 Metal 最小测试也曾在双源混合时产生像素差异并破坏桌面；因此不能在已经损坏的 GPU
状态下继续比较输出。用户反馈是本机验证，不代表其他 GPU 或 macOS 版本已验证。

## 启用与诊断

使用验证成功的参数组合，修改后需要重启：

```text
-NRedImmediateSync -NRedSrdShared -NRedLegacyBlend
```

`-NRedLegacyBlend` 会同时启用前两项；单独的旧参数保留作兼容和对照。
IORegistry 的 `NRedLegacyBlendEnabled` 表示 hook 已注册，`Applied` 为有上限的聚合计数。
WindowServerPID / WindowServerApplied 标记完整写入并恢复 RX 的进程；D00–D15 记录拒绝和失败阶段。
`NRedImmediateSyncRevision=3` 标记当前动态匹配候选；`Requested=1` 表示读取到了启用参数。
`NRedImmediateSyncRejected` 的 1/2/3/4 分别表示符号缺失、hook 失败、PCI 不匹配和
非 Darwin 25。缓存格式或动态内容核验失败记录为 `NRedImmediateSyncD04`。
`DynamicMatched=1` 表示动态计划核验通过，`DynamicApplied=1` 表示实际写入并恢复 RX；
`DynamicRejected=1` 表示未知缓存不兼容或传入了限定参考缓存的 scratch 参数。
`CacheUUID0`–`CacheUUID3` 按四个小端 UInt32 记录实际缓存 UUID，`DynamicReads` 记录核验读取次数。

运行时仅写进程私有 COW 页面，不写磁盘上的系统缓存。先核对全部补丁与 RX 保护，再写入，
部分失败时回滚并恢复原保护，始终保留原系统调用的参数、返回值和错误结果。

## Metal Transfer 与 scratch

`-NRedComputeScratch` 默认关闭，包含 LegacyBlend 基线。已撤回全局强制 1×1 kernel；
旧实验参数 `-NRedTransfer1x1` 保留兼容入口，转向 ComputeScratch 并记录 `Redirected`。
不修改 CoreMedia 偏好或系统缓存，检测到残留的旧策略会拒绝混用。
Scratch 支持范围取自 NRed 当前接受的 PCI 分类：`0x15D8`、`0x15DD`、`0x15E7`、
`0x1636`、`0x1638`、`0x164C`。每个设备使用只匹配该 PCI ID 的 scratch 载荷，因此同进程内的
Vega 独显继续按原 SE 数分配。动态 UUID 路径尚无可重定位的 scratch 计划，未知缓存会拒绝 scratch。

`NRedComputeScratchEnabled`／`Applied` 表示注册及完整写入，WeatherPID／WeatherApplied
最多记录四次故障应用的修正；这些字段不能代替真实运行结果。
修复后的原生 kernel 已连续转换 48 帧，13 次转换与独立 CPU vImage 对照误差 ≤2。
安装后的整机性能、所有 HDR／格式和长期负载尚未完成验收。

## 生成与回归

以下命令需要 macOS、Python 3、Xcode 工具和对应版本的本机系统文件；各子进程限时 60 秒。
生成文件通过生成器更新，不手工修改：

```sh
python3 tools/build_surface_sync_patch.py
python3 tools/test_surface_sync_patch.py
python3 tools/test_legacy_blend_gate.py
python3 tools/test_surface_sync_bridge.py
python3 tools/test_surface_sync_dynamic.py --sanitize
python3 tools/test_surface_sync_dynamic_cache.py
python3 tools/test_compute_scratch_patch.py
python3 tools/test_compute_vtable.py
```

测试覆盖完整初始化结果、混合分支、逐 PCI ID scratch 选择、Vega 独显原生 SE 和溢出语义、ABI 槽位、版本拒绝、
部分写入、三页回滚和保护恢复。
Revision 2 起逐设备执行真实初始化与配置覆盖载荷、穷举完整 16 位 PCI ID 空间，
确保补丁只修改本机核显；同进程内其他 GPU 的同步与 SRD 设置保持原生行为。
Revision 3 增加真实桥接代码的 63 组动态缓存测试：UUID、ASLR、函数位置改变，完整函数与
引用目标拒绝，重复匹配、解析上限、六页回滚，以及 ASan/UBSan 检查。只读真实缓存测试核实
改变 UUID 和模拟 ASLR 后生成的补丁地址；没有把这些自动回归当作新系统版本的整机验证。
构建与静态分析仍按仓库三种配置执行。临时结果写入 `build/`，不需要历史实验包或日志。

只读 pipeline 检查和可选 GPU 回归：

```sh
clang -fobjc-arc -Wall -Wextra -Werror -framework Foundation -framework Metal \
  tools/test_metal_dual_source.m -o build/test-dual-source
build/test-dual-source --inspect-only
build/test-dual-source --render
```

修复后的普通/双源 pipeline 应显示 `DISABLE_DUAL_QUAD=1`。
`--render` 先检查该位，再比较实际像素，首次错误或超时即停止；只在重启后桌面正常的状态运行。

Metal Transfer 检查工具默认不提交 GPU；`--render` 要求修复已生效，
`--candidate-render` 仅为自身进程应用 scratch 修复后执行像素回归：

```sh
clang++ -std=c++17 -fobjc-arc -I NootedRed -framework Foundation -framework Metal \
  -framework VideoToolbox -framework CoreVideo -framework Accelerate \
  tools/test_metal_transfer.mm -o build/test-metal-transfer
build/test-metal-transfer --inspect-only
```

原生双源输出语义参考 [Apple Metal 文档](https://developer.apple.com/library/archive/documentation/Miscellaneous/Conceptual/MetalProgrammingGuide/WhatsNewiniOS10tvOS10andOSX1012/WhatsNewiniOS10tvOS10andOSX1012.html)，
RB+ 兼容状态参考 [AMD/Mesa 实现](https://chromium.googlesource.com/chromiumos/third_party/mesa/+/1c702a82397bb0c84bee1478912c0e5b69f95eb5/src/amd/vulkan/radv_pipeline.c)。
