# VPF 兼容与拒绝矩阵

> 本表记录当前代码的准入边界。合成缓存和用户态测试不等于实机硬件验收。

## 1. 系统与启动入口

| 系统 | Darwin | 显示入口 | 视频入口 | VPF 状态 |
| --- | --- | --- | --- | --- |
| macOS 14 Sonoma | 23 | `-NRedDisplayFix`；`UserLegacyData::metal(23)` 精确配置 | `-NRedVideoFix`；安全守卫证据缺失，拒绝用户态视频补丁 | Manifest 不覆盖 OS23 精确配置 |
| macOS 15 Sequoia | 24 | `-NRedDisplayFix`；动态 `resolveSequoia` + 动态 Submit | `-NRedVideoFix`；`UserSequoiaVideoData` 动态解析 + 守卫 24 | Manifest 覆盖 |
| macOS 26 Tahoe | 25 | `-NRedDisplayFix`；动态 `resolve` + 静态 SRD/LegacyBlend/Scratch/Submit | `-NRedVideoFix`；`UserVideoDecodeData` 动态解析 + 守卫 25 | Manifest 覆盖 |

没有新增 VPF boot-args；`NRedECFix` 等与 VPF 无关。

## 2. 显示组

支持的 PCI ID（`UserSurfaceSyncData::DevicePatches`）：

`0x1638`、`0x15D8`、`0x15DD`、`0x15E7`、`0x1636`、`0x164C`。

| 功能 | Darwin 23 | Darwin 24 | Darwin 25 | 证据 |
| --- | --- | --- | --- | --- |
| Immediate / SRD / LegacyBlend | OS23Metal 精确配置 | `UserSequoiaSurfaceData` 动态完整函数 | `UserSurfaceSyncData` 静态表 + 完整函数核验 | 动态：`RelocationAwareFullFunction`；静态：`ExactValidatedBuild` |
| ComputeScratch | 不启用 | `ScratchResolverData` 动态；六设备 | 同左 | `RelocationAwareFullFunction` + 设备表 |
| SubmitGuard | 不进入动态路径 | `Submit` 完整函数动态定位 | `Submit` 完整函数动态定位 | `RelocationAwareFullFunction` |
| OpenGL SE/CU + DualQuad | 页内完整块；参考页快速路径 | 同左；15.8.1 参考页已核验 | 同左；当前 26.7.2 驱动不匹配夹具 25 | 精确：`ExactKnownPage`；动态：`FullPageLocalBlock` |
| 守卫页 | 无 | `vm_allocate` 私有页 + 载荷核验 | 同左 | 运行时生成，无静态站点 |

`UserPatchCoordinator` 在显示与视频模块均成功安装时调用原生入口一次，再以 `vm_map`
为租约键执行显示和视频回调；映射函数不可用时退回 task 身份，映射结果为空时跳过。
单模块安装仍走已有路径。Metal 主渲染组与 SubmitGuard 使用
核验后的计划和共享事务执行器，SubmitGuard 先于 RenderCore，入口未恢复时保留跳板页。
OpenGL 使用已验证页内的完整块匹配，参考页证据用于交叉核验而非固定写入偏移。
内核证据和离线语义报告不改变运行时准入。

Darwin 23 的 Metal 精确 profile 由显示清单单独记录 LegacyBlend gate；组规划器保留
LegacyBlend 与 SubmitGuard，拒绝不支持的 SRD/Scratch。已知部分 Darwin 25 构建与参考
OpenGL 数据不匹配时会拒绝安装，不能通过放宽运行时条件解决。

关键拒绝/诊断键：

- `NRedImmediateSyncRejected`：参数/MapGate/符号/路由失败。
- `NRedImmediateSyncDynamicRejected`：动态显示计划核验失败。
- `NRedLegacyBlendRejected`：OS23 精确 profile UUID/字节不匹配。
- `NRedSubmitGuardRejected` / `NRedSubmitGuardDynamicRejected`：提交入口或载荷不匹配。
- `NRedSubmitGuardRollbackFailed`：入口无法恢复；保留目标页，不自动重试。
- `NRedPatchCoordinatorEnabled/Skipped/Rejected/Peak`：两个模块协调、单模块回退、Hook 安装失败或
  活动 map lease 峰值。
- `NRedDisplayVPFPlanState/Sites/Verified`：本进程 Metal 计划读回状态，仅诊断用。
- `NRedOpenGLVPFRevision/PlanVerified/ManifestMask/EvidenceMask`：OpenGL 页计划与参考证据
  交叉核验状态；不代表 GPU 已执行。
- `NRedKernelDisplayEvidenceRevision`、`NRedKernelGFX9*`、`NRedKernelKext*`、
  `NRedKernelFramebuffer*`、`NRedKernelScanout*`、`NRedKernelFlip*`：只读证据；
  不表示 HDR/Flip/GFXOFF 已修复。

## 3. 视频组

支持的 VCN2.2 PCI ID（`VCNCapabilities`）：`0x15E7`、`0x1636`、`0x1638`、`0x164C`。
Manifest 覆盖和合成测试不能证明各设备已通过实机验收。

| 功能 | Darwin 23 | Darwin 24 | Darwin 25 |
| --- | --- | --- | --- |
| 用户态视频补丁 | **拒绝**：`NRedVCNUserSafetyProfileMissing=1`、`NRedVCNUserRejected=6` | `UserSequoiaVideoData` 动态；所有 UUID | 参考 UUID `31374756-89B4-34E8-A34F-56C5D57216A1` 静态快路径；其他 UUID `UserVideoDecodeData` 动态 |
| 安全守卫 | 无 | `UserVideoGuard24Data`；先于设备适配 | `UserVideoGuard25Data`；先于设备适配 |
| FairPlay | 不启用用户态视频补丁 | entitlement `com.apple.private.fpsd.client`；只选 AMD 解码子集 | 同左 |
| AppleGVA / 编码 | 原生 | 原生策略；编码补丁不在 FairPlay 子集 | 同左 |
| 受保护视频黑屏 / 固件证书 | 未解决 | 未解决 | 未解决 |

关键拒绝/诊断键：

- `NRedVCNUserRejected`：能力/符号/路由/MapGate 初始化拒绝。
- `NRedVCNUserSafetyProfileMissing`：Darwin 23 守卫证据缺失。
- `NRedVCNUserDynamicRejected`：动态视频计划核验失败。
- `NRedVCNUserGuardRejected`：守卫入口/载荷/函数核验失败。
- `NRedVCNGuardRollbackRetainedPage`：守卫回滚无法确认时保留页。
- `NRedVCNUserMixed` / `NRedImmediateSyncD08`：混合状态拒绝。

## 4. Manifest 覆盖

| FeatureId | Darwin | 设备 | 站点 | 说明 |
| --- | --- | ---: | ---: | --- |
| `Vcn2Decode` | 24 / 25 | 4 | 23/实例 | 视频补丁表；含编码相关站点，但 FairPlay 仍按位掩码选解码子集 |
| `Vcn2SafetyGuard` | 24 / 25 | 0（共用） | 5/实例 | Identity 约束；运行时入口补丁由 `adaptGuard` 取实际 Plan |
| `DisplaySRD` | 24 | 6 | 4/实例 | `UserSequoiaSurfaceData` 动态完整函数 |
| `DisplaySRD` | 25 | 0 | 2 | 静态 `SrdSharedPatches`；`ExactValidatedBuild` |
| `DisplayLegacyBlend` | 25 | 0 | 3 | 静态 `LegacyBlendPatches` |
| `DisplayComputeScratch` | 24 / 25 | 0 | 0 | `ScratchResolverData` 函数证据；实际站点来自设备表 |
| `DisplayPipelineConstructor` | 24 / 25 | 0 | 1 | `UserSurfaceConstructorData` |
| `DisplaySubmit` | 24 / 25 | 0 | 0 | Submit 完整函数；偏移运行时返回 |
| `DisplaySubmitGuard` | — | — | — | 跳转/守卫载荷运行时生成，无静态站点 |
| `DisplayOpenGLHwInfo` | 23 / 24 / 25 | 6 | 1/实例 | DVPF：完整页块，页内 256 B |
| `DisplayOpenGLDualQuad` | 23 / 24 / 25 | 6 | 1/实例 | DVPF：页内 142 B |
| `DisplaySubmitGuard`（DVPF） | 23 / 24 / 25 | 0（共用） | 1/实例 | 入口跳转模板；目标地址运行时填写 |
| `KernelGFX9Capabilities` / `KernelDCNScanout` / `KernelFramebuffer` | 23 / 24 / 25 | 0（共用） | 0 | 仅符号/ABI 描述，不进入用户态事务 |
| `Vcn2Encode` | — | — | — | 未拆独立 Manifest；编码站点在视频表中 |
| `AppleGVASelection` / `FairPlayNativeDecodeSubset` | — | — | — | 由现有 FairPlay 策略与掩码控制 |

DVPF 的 `DisplayManifestData.hpp` 由 `tools/dvpf/generate_display_manifest.py` 生成；
上表 DVPF 行只描述只读清单，不改变运行时准入。

## 5. 证据边界

- 合成缓存通过不代表实机通过；当前最终生命周期 Hook 尚未完成实机验证。
- Manifest 中存在站点不代表功能在所有设备或系统中已启用；运行时仍受 boot-args、PCI、
  entitlement、守卫及 UUID/字节核验约束。
- 视频补丁表不代表其他视频格式或受保护视频已经支持。
- Video 生产路径使用 Manifest、Planner、VerifiedPatchPlan 和共享缓存事务执行器；
  Display 的 Resolver 和准入仍沿用现有入口。
- 离线语义分析与补丁站点审计不放宽运行时准入或拒绝条件。
