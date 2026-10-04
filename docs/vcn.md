# Vega 核显 VCN 解码接入

## 范围

实验性接入按 `VCNCapabilities.hpp` 的能力表选择设备，内核与用户态共用这一判定。
当前开放 PCI `1002:15E7 / 1636 / 1638 / 164C` 的 VCN 2.2 路径；
`1002:15D8 / 15DD` 保留分类但不接入 VCN1。能力表必须与 NRed 原有设备分类一致，
生成器发现重复、遗漏或未经核实的 VA 分类时拒绝生成。

| PCI ID | NRed 家族 | 视频代际 | 本次接入 |
| --- | --- | --- | --- |
| `15D8 / 15DD` | Picasso / Raven（含 Raven2） | VCN 1.x | 拒绝，尚未实现 VCN1 桥接 |
| `1636 / 164C` | Renoir 系列 | VCN 2.2 | 显式启用的实验路径，待实机验收 |
| `1638 / 15E7` | Cezanne / Barcelo 系列 | VCN 2.2 | 保留 Cezanne 路径，新增设备待实机验收 |

代际依据 [Linux 内核 AMD 硬件组件表](https://www.kernel.org/doc/html/v6.17/gpu/amdgpu/amd-hardware-list-info.html)。
同一视频代际是复用现有桥接的依据，不代表 Apple 驱动已经支持或新设备已经硬解成功。

内核仍要求 Darwin `25.6`；用户态仍要求生成数据指定的参考 `x86_64h` 缓存 UUID
`31374756-89B4-34E8-A34F-56C5D57216A1`。本次泛化设备能力，未泛化内核 ABI 或视频缓存布局。
使用 `-NRedVCN` 显式启用；保留已验证渲染基线的启动参数。
默认关闭，未增加独显或其他 macOS 版本的支持。

## 实现

X5000 加速器启动前请求可选加载 X6000；只有同步加载、两侧 hook 准备成功才接入
原生 VCN2 引擎、视频上下文与客户端。加载失败／延迟时不在既有实例中途启用。
启动 hook 还核对真实 PCI vendor 和设备 ID 必须与 NRed 检测的核显一致；
另一设备即使在能力表中，也不能触发本机接入。
插件库依赖保持不变，跨驱动虚调用按本机虚表修正，包含 GFXOFF 等待配对。

四种 VCN2 设备复用当前 X5000/X6000 的引擎槽位、VCN 通道、GFX9 SML 与 AddrLib 路径。
VA identify 载荷分别按本机 PCI 生成，只将该设备映射到已有的视频分类 5；
该分类用于选择 VCN2 解码能力，不把核显的 GFX/AddrLib 家族改成 Navi。
其他 PCI 分类、IOKit 错误出口和错误 type 请求保留原生行为。
视频工厂、RV AddrCreate、swizzle 修正与 AppleGVA 选择沿用既有实现。

用户态补丁仅写入匹配缓存 UUID、原始指令及 RX 保护的进程私有 COW 页。
失败时回滚已写字节并恢复保护；保留原生返回值。生成头文件不手工修改。
AppleGVA 在无 Intel、仅 AMD 可用的机型回退出口选择已有的 AMD 解码器，
保留混合 GPU、已知机型、显式设备请求及无可用 GPU 的原生选择。

## 生成与验证

在当前适配的 macOS 主机执行；不匹配的驱动／缓存不得直接复用补丁：

```sh
python3 tools/build_vcn_kernel_patch.py
python3 tools/build_video_decode_patch.py
python3 tools/test_vcn_candidate.py
python3 tools/test_vcn_addr_profile.py
python3 tools/test_vcn_user_bridge.py
python3 tools/test_vcn_native_load.py
python3 tools/test_vcn_engine_bridge.py
python3 tools/test_vcn_idle_calls.py
python3 tools/test_vcn_gva_choice.py
python3 tools/test_surface_sync_bridge.py
python3 tools/test_compute_vtable.py
```

中间文件位于 `build/vcn/`，子进程限时 60 秒。
VA 回归对每份载荷穷举完整 16 位 PCI 空间，并验证 IOKit 失败和错误请求；
启动回归直接编译实际 capability gate，覆盖真实 provider 不符、加载延迟与失败。
引擎回归编译实际 X5000 槽位写入、分配失败、跨驱动 cast 尺寸检查和 SML/GFX access 路由；
COW 回归在四种设备上覆盖每个部分写入、保护失败、缓存与混合补丁拒绝及返回值保留。
这些是 CPU 模拟，不能代替硬件初始化、视频码流与长期运行测试。
另执行 Debug、Research Release、Release 的构建与静态分析，
并以 `tools/test_vcn_package_dependencies.py <构建后的 Info.plist>` 检查加载依赖。

## 实机验收与限制

既有 Cezanne `1638` 的正常尺寸 H.264／HEVC 短视频已观察到硬解与软件像素摘要一致；
64×64 H.264 仍失败。新增三种设备尚无等价实机结果。
Hackintool 检查曾触发 VCN0Dec 未完成与 WindowServer 看门狗；GFXOFF 调用修正已通过
离线回归，消除挂起的实机验证尚未完成，不能声明稳定或完整解码支持。

验收覆盖 Hackintool 创建／销毁解码器、至少五分钟空闲后再启动、持续播放与桌面响应。
保留有次数上限的诊断字段／日志；记录触发时间、硬解属性及 `.gpuRestart`／WindowServer 报告。
`NRedVCNGVASelectionFix=1` 表示新选择补丁已随完整用户态事务写入并恢复 RX。
Apple TV 在线观看的密钥请求及系统网络／音频 panic 需要独立验收，renderer 选择修正
尚不能证明这些故障已解决。

## 诊断与回退

用 `ioreg -l -w0 -r -n IGPU | rg 'NRedVCN'` 读取诊断。
`NRedVCNRequested=1`、`CapabilityVersion=1`、`DeviceID` 和 `Generation` 表示能力判定已执行；
`Generation=0x0202` 表示 VCN 2.2，`0x0100` 只表示 VCN1 家族（不是精确的 IP 小版本）。
`NRedVCNRejected` / `UserRejected` 的 3/4/5 分别表示设备未知、内核布局未核实、视频代际未实现；
用户态的 1/2 仍表示符号缺失 / hook 失败。

`NativeLoadReady`、`EngineAllocated`、`LastHardwareInit`、`UserApplied` 和 `DecoderPID`
分别记录内核准备、引擎分配、真实硬件初始化、用户态事务和解码进程。
`UserD04=1` 表示缓存不匹配，`UserD06=1` 表示代码不匹配；
`UserEnabled` 只表示 hook 注册，不能作为硬解成功证据。

验收使用 `-NRedVCN`，记录 CPU/PCI、`sw_vers`、`uname -r`、诊断字段及解码器的
`UsingHardwareAcceleratedVideoDecoder` 属性，并对 H.264/HEVC 输出与软件解码做像素比较。
保留可启动的 EFI；发生异常时恢复旧 kext、移除 `-NRedVCN` 并重启。
