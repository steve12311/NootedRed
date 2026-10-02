# Cezanne Metal 渲染修复

## 修复内容

适用范围为已核实的 PCI `0x1638`、Darwin 25.6 / macOS 26.7.1，以及生成数据中指定的
x86_64h dyld cache UUID。其他版本或不匹配的指令会拒绝应用，默认不开启用户态补丁。

- `X5000` 的 compute submit 虚表修复跳过 Itanium ABI 的两个头部槽位，并核对原提交函数地址。
- `UserSurfaceSync` 使用原生立即屏障路径，并为目标设备选择原生 shared SRD 表分配。
- LegacyBlend 修复 pipeline 构造中的 RB+ 能力分支：缺少能力报告时，仍计算
  `dualSource || !allowRBPlus`，正确设置 `CB_COLOR_CONTROL.DISABLE_DUAL_QUAD`。
- ComputeScratch 仅在目标设备的 scratch 分配中使用物理的一个 SE，避免虚拟 4 SE
  将 scratch 环从 256 waves 配置为 1024 waves；原生 kernel、设备报告的拓扑及 VCN 不变。

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

运行时仅写进程私有 COW 页面，不写磁盘上的系统缓存。先核对全部补丁与 RX 保护，再写入，
部分失败时回滚并恢复原保护，始终保留原系统调用的参数、返回值和错误结果。

## Metal Transfer 与 scratch

`-NRedComputeScratch` 默认关闭，包含 LegacyBlend 基线。已撤回全局强制 1×1 kernel；
旧实验参数 `-NRedTransfer1x1` 保留兼容入口，转向 ComputeScratch 并记录 `Redirected`。
不修改 CoreMedia 偏好或系统缓存，检测到残留的旧策略会拒绝混用。

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
python3 tools/test_compute_scratch_patch.py
python3 tools/test_compute_vtable.py
```

测试覆盖完整初始化结果、混合分支、scratch 设备限制和溢出语义、ABI 槽位、版本拒绝、
部分写入、三页回滚和保护恢复。
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
