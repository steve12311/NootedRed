# 修复功能启动参数

本版只使用以下三个入口，不兼容此前的修复启动参数。

| 参数 | 功能 | 省略时 |
| --- | --- | --- |
| `-NRedDisplayFix` | 花屏／卡顿修复，包含 SubmitGuard；按系统选择渲染补丁 | 显示修复全部关闭 |
| `-NRedVideoFix` | VCN 硬件视频加速，包含现有解码和编码路径 | VCN 桥接关闭 |
| `NRedECFix=1` | 使用固件报告的 EC 背光来源 | EC 服务关闭 |

三项均启用的启动参数：

```text
-NRedDisplayFix -NRedVideoFix NRedECFix=1
```

只有需要主动切换固件控制来源时，才把 `NRedECFix=1` 改为 `NRedECFix=2`。
`=2` 沿用强制模式，仍须核验固件调用结果和报告来源；本机固件的来源读取固定返回 2，
不能仅凭该值证明控制权切换。`=0`、其他无效值或省略参数均不启动 EC 服务。

显示开关在 macOS 14 启用 LegacyBlend 和 SubmitGuard，在 15/26 启用
SrdShared、LegacyBlend、ComputeScratch 和 SubmitGuard，保留原生同步策略，不再启用 ImmediateSync。
三个系统的显示开关还启用 OpenGL SE/CU 和条件 DualQuad 修复，无需新增参数。
OpenGL 在当前已验证页面内匹配完整修复块及引用，不主动读取驱动文件或启动准备任务。
`NRedOpenGLRevision=5`、`NRedOpenGLPageLocal=1` 标记此实现；加载 OpenGL 应用后检查
`NRedOpenGLSEApplied` / `NRedOpenGLDualQuadApplied`。`DynamicApplied` 仅标记两项均经动态路径命中。
详细匹配边界与本机页面校验见 [OpenGL 修复](opengl-display-fix.md)。
`NRedDisplayFixVersion=2` 标记这个组合；`NRedImmediateSync*` 继续作为历史页面事务诊断名称，
`NRedImmediateSyncDisabled=1` 表示立即同步修复未启用。
**本版不含 HDRIntegerFilter 候选的运行代码与补丁数据。**
设备、缓存、完整代码和 ABI 核验仍然生效，具体版本边界见
[多系统适配](macos-multi-os-compat.md)。

显示、视频和 EC 三项可独立启用，关闭某一项只需移除对应参数并重启。
插件全局关闭 `-NRedOff`、调试参数及上游的 `AMDBacklight` 设置不在本次更名范围内；
`AMDBacklight=0` 仍会阻止 EC 服务启动。

以下旧修复参数应从配置中移除，本版不会读取：

```text
-NRedImmediateSync -NRedSrdShared -NRedLegacyBlend -NRedComputeScratch
-NRedTransfer1x1 -NRedVCN -NRedNoSubmitGuard -NRedHDRIntegerFilter
NRedECBacklight=1 NRedECBacklight=2
```

诊断属性仍沿用模块名，例如 `NRedVCN*`、`NRedComputeScratch*`、`NRedECBacklight` 服务。
这些名称是运行状态，不是旧参数兼容入口。
`Enabled` 仅表示已注册；确认实际补丁写入还需检查 `Applied`／`WindowServerApplied`。

## FairPlay 客户端兼容

FairPlay 兼容随 `-NRedVideoFix` 默认启用，在 macOS 14/15/26 / Darwin 23/24/25 的
受支持 VCN 2.2 APU 上生效，不再需要 `-NRedDRMVideo`。所有持有 `com.apple.private.fpsd.client=true`
权限的进程统一应用 AMD 驱动侧解码子集及内容保护工厂 PSP 适配，不再按 Music／TV 等进程名区分；
AppleGVA 与编码代码保持原样。14 按完整原始页面核验，选择 13 项 AMD 解码补丁；
15/26 选择 18 项并支持动态匹配。三个系统采用相同策略，但补丁条目随已核实的版本数据不同。
`NRedDRMVideoRevision=4` 标识三系统统一权限策略，不代表 DRM 视频已修复。
既有 Music 实测为普通音乐正常、受保护 MV 有声且进度推进，但视频仍黑屏；统一版本的
Music／TV 实机回归尚待验证。范围、诊断和验证步骤见 [FairPlay PSP 兼容说明](fairplay-psp-compatibility.md)。
