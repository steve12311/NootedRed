# macOS 14 / 15 / 26 的 Metal 与 VCN 适配

本轮参考 [weachy/NootedRed 固定提交 3b37dfc](https://github.com/weachy/NootedRed/tree/3b37dfc6282016608962b2746d7809765ad121f4)，
将对应的旧系统数据接入现有的私有 COW 页面事务与 VCN 内核桥接。
参考数据、来源文件 SHA256 和版权记录保存在 `tools/fixtures/legacy-compat.json`；
`tools/import_legacy_compat.py` 生成两个数据头，`--check` 检查可复现性。

## 版本与参数

启动参数已统一为三个入口，配置示例与旧参数清单见 [启动参数](boot-arguments.md)。

| 功能 | macOS 14 / Darwin 23 | macOS 15 / Darwin 24 | macOS 26 / Darwin 25 |
| --- | --- | --- | --- |
| SubmitGuard | 14.8.9 / 23J631 的缓存配置 | 完整提交函数动态定位 | 原有参考缓存配置 |
| `-NRedDisplayFix` 中的 LegacyBlend | 完整页面核验后的条件修复 | 完整构造函数动态匹配 | 保留原有静态／动态匹配 |
| `-NRedVideoFix` | 独立 ABI 与调用点；精确视频页面 | 独立 ABI；完整函数动态解析 | 保留原有 ABI 与动态解析 |
| ImmediateSync | 关闭 | 关闭 | 关闭 |
| OpenGL SE/CU / DualQuad | 页内完整修复块匹配，参考页快速路径 | 同左，本机参考页已核验 | 同左，本机参考页已核验 |
| SrdShared / ComputeScratch | 尚未适配 | 启用，独立函数数据 | 启用 |
| FairPlay 客户端 | 13 项 AMD 解码子集及 PSP 工厂适配 | 18 项 AMD 解码子集及 PSP 工厂适配 | 18 项 AMD 解码子集及 PSP 工厂适配 |

14.8.9 的缓存 UUID 为 `4C1441FD-8A54-3DC2-863D-49110B946A25`，
15.8.1 为 `42EC2890-BC40-317E-BB27-45142BFF7F26`。
版本入口允许 Darwin 23/24/25 进入核验，**不代表这些系统的所有小版本均已兼容**。
14 的未知 UUID 或完整页面不符会拒绝；15/26 允许相同实现的缓存重排，完整函数或外部目标不符即拒绝。
内核 getter／调用点仍按系统核验；不套用 Tahoe 的固定偏移。

SubmitGuard 随 `-NRedDisplayFix` 启用，移除显示参数即关闭整组修复。
14 不执行 SrdShared、Scratch；15/26 启用对应补丁并保留原生同步策略，不启用 ImmediateSync。
本版不含 HDR 候选。
VCN 仍仅接入 `15E7 / 1636 / 1638 / 164C` 的 VCN 2.2，Raven/Picasso 的 VCN1 继续拒绝。

## 核实并采用的优化

- 参考分支的 LegacyBlend allow 写入偏移与其原始页面不符：14 的 `0x1DB` 实际对应
  `0x1E1`，15 的 `0x185` 对应 `0x198`。本实现只将硬件能力 gate 的 `75 0B` 改为
  `EB 0B`，完整保留后续 `dualSource || !allowRBPlus` 计算和状态写入。
  普通混合且允许 RB+ 时继续使用原生优化，不强制禁用所有 pipeline 的双 quad。
- 14/15 的识别函数与本地已验证函数除了三个外部相对引用外逐字节一致。
  按实际 PCI 重新生成载荷，保留其他设备、IOKit 失败和错误请求类型的原生分类。
- 地址转换完整函数与本地原函数一致，复用 RV 专用地址库设置；视频处理器分支同时指定
  GFX9 类型参数，保留原有非 RV 路径。
- VCN 14/15 的布尔 getter 为 12 字节，26 为 14 字节，分别匹配。
  调用点表也分别选择，保留通道与引擎等待路径的 GFXOFF 成对调用，并检查 rel32 范围。
- SubmitGuard 核对完整已安装跳板及返回地址；部分入口写入或权限恢复失败时恢复原入口。
  安装失败释放尚未引用的页；入口无法确认恢复时保留目标页并报告
  `NRedSubmitGuardRollbackFailed`，避免残留跳转引用已释放内存。
  守卫页恢复当前和最大 RX 权限，`NRedSubmitGuardRevision=6` 标记此实现。
- 保留现有计算提交虚表地址点修复、已用命令内容和同批流保护。
  FairPlay 客户端在三个系统上统一采用 AMD 解码子集，排除 AppleGVA 和编码代码；
  旧系统新增的 PSP 工厂比较位同样受完整页面核验保护。

14 视频事务核验九个完整页面，实际只修改生成计划内的范围。
15 基于本机 Macintosh SSD 的 15.8.1 / 24H32 只读共享缓存生成独立数据，
包含 SRD、456 字节 Scratch、视频 pitch、8 处 swizzle 及 48 字节编码反馈修正。
生成器复用 Tahoe 的载荷逻辑与重定位提取，参考函数摘要见 `tools/fixtures/sequoia-compat.json`；
运行时不要求 UUID 一致。14 保持既有范围。HDR 候选未集成到本版运行代码与产物。

## 验证与实机验收

离线检查（每个子进程限时 60 秒）：

```sh
python3 tools/import_legacy_compat.py --check
python3 tools/test_multi_os_compat.py
python3 tools/test_legacy_blend_multi_os.py
python3 tools/test_legacy_bridges.py --sanitize
python3 tools/test_vcn_kernel_install.py --sanitize
python3 tools/test_vcn_kernel_abi.py --sanitize
python3 tools/test_submit_guard_safety.py
python3 tools/test_surface_sync_bridge.py
python3 tools/test_surface_sync_dynamic.py
python3 tools/test_compute_cache_dynamic.py --sanitize
python3 tools/test_video_cache_dynamic.py --sanitize
python3 tools/test_vcn_user_bridge.py
python3 tools/test_vcn_native_load.py
python3 tools/test_ec_backlight_service.py
python3 tools/test_ec_backlight_service.py --thread-sanitizer
python3 tools/test_fix_package.py path/to/NootedRed.kext
```

旧系统原生 gate/store 块在 CPU 上执行 16,384 组对照，覆盖能力、双源、RB+ 开关及随机控制位。
桥接检查覆盖 Metal 六种 APU、视频四种设备、每个部分写入、逐页保护失败、幂等、缓存和内容拒绝。
内核安装回归执行实际安装器，检查三套调用点、GFXOFF 配对、跳转目标和 route 失败回滚。
这些测试不会提交 GPU 命令，不能替代目标机器的驱动初始化、视频播放与画面验收。

安装前记录 GPU 型号、完整 macOS 版本／构建号及缓存 UUID。重启后核对
`NRedSubmitGuardWindowServerApplied`、`NRedLegacyBlendWindowServerApplied`、
`NRedVCNX5000ABIValidated`、`NRedVCNX6000ABIValidated`、`NRedVCNUserApplied`，
再验证菜单／双源混合、持续 H.264／HEVC 解码及编码、空闲后重新播放和睡眠唤醒。
保存首次异常时间与 `.gpuRestart`／`.panic`；目前尚无本轮三个系统的实机验收结果。

Sequoia 专项回归（`CACHE15` 指向只读挂载镜像的 `System/Library/dyld`）：

```sh
python3 tools/build_sequoia_surface.py --cache "$CACHE15"
python3 tools/build_video_decode_patch.py --sequoia-cache "$CACHE15"
python3 tools/test_surface_sync_patch.py --sequoia
python3 tools/test_compute_scratch_patch.py --sequoia-cache "$CACHE15"
python3 tools/test_video_encode_patch.py --sequoia-cache "$CACHE15"
python3 tools/test_cache_resolver_reference.py --sequoia-cache "$CACHE15"
python3 tools/test_compute_cache_dynamic.py --sequoia --sanitize
python3 tools/test_video_cache_dynamic.py --sequoia --sanitize
```
