# VPF 架构

VPF 为用户态共享缓存补丁提供清单、核验、依赖判断和事务执行能力。Video 与 Display
复用共享事务基础设施；显示域的页处理、协调器和证据边界见
[DVPF 显示架构](dvpf-architecture.md)。
兼容边界见 [兼容矩阵](vpf-compatibility-matrix.md)，验证范围见 [验证说明](vpf-validation.md)。

## 组件

- `NootedRed/VPF/PatchManifest.hpp` 和 `PatchManifestData.hpp`：功能、设备、系统、映像、
  函数和补丁站点的只读描述。生成头来自仓库中现有的已验证补丁数据。
- `VerifiedPatchPlan.hpp` 和 `ResolverAdapters.hpp`：将 Resolver 输出适配为只读补丁计划，
  检查地址、站点范围、重叠及原始/已修改状态。计划引用 Resolver 缓冲，调用方须保证其生命周期。
- `DependencyPlanner.hpp`：根据功能依赖、冲突、设备、系统及已核验证据确定安装顺序。
- `CapabilityState.hpp`：维护每个地址空间的能力状态和失败隔离状态；受调用方锁保护。
- `TransactionExecutor.hpp`：共享缓存页面事务，负责权限检查、写前准备、写入、读回、
  恢复和失败回滚。
- `tools/vpf/`：清单生成与校验、计划对照、基线、离线语义证据和补丁站点审计。
  离线工具不生成可安装的语义变体，也不改变运行时准入。

## Video 路径

共享区域检查首先调用原生入口。通过现有 boot-args、VCN 能力、系统、设备与 FairPlay
策略检查后，Video 和 Guard Resolver 对映像及补丁字节进行核验。适配器生成
`VerifiedPatchPlan`，依赖规划器要求 SafetyGuard 先于 Decode，随后事务执行器按计划安装。
FairPlay 客户端仍使用既有解码子集与掩码。

Video 单模块路径按 `vm_map` 串行化；启用协调器后使用协调器的租约键。同线程重复进入
同一租约时拒绝事务。回滚失败会隔离该地址空间；账本在最终 map 销毁时清理，生命周期 Hook 的目标无法核验或安装
失败时拒绝启用 Video。守卫页的分配、验证与保留遵循现有失败处理。

`NRedVCNVPF*` 属性用于报告路径、规划和账本状态。单个地址空间的安装成功不代表所有进程
均已安装，也不代表通过实机验收。

## Display

显示域复用同一套 `FeatureId`、`VerifiedPatchPlan`、`DependencyPlanner` 和 `CapabilityLedger`，
但保留独立的执行后端和证据要求：

- `DisplayManifest.hpp` / `DisplayManifestData.hpp`：OpenGL 页内块、Metal 共享缓存站点、
  SubmitGuard 跳板和内核描述条目。Metal 站点直接引用 `PatchManifestData.hpp`，不复制字节。
- `DisplayPlan.hpp` / `DisplayPlanAdapters.hpp`：`VerifiedPagePlan` 只接受验证值 `1`/`0xF`、
  `tainted=0`、`nx=0`、精确 vnode 和页内完整块；`DisplayPlanView` 统一页计划与共享缓存计划。
- `DisplayDependencyPlanner.hpp`：组闭包、依赖/冲突和拒绝原因；不读取 boot-args，不安装补丁。
- `DisplayCapabilityState.hpp`：共享账本的组聚合视图，不另建地址空间账本。
- `ExecutableTrampolineResource.hpp`：SubmitGuard 跳板生命周期；入口未恢复时保留页面。
- `UserPatchCoordinator.hpp/.cpp`：在最外层 `_shared_region_check_np` Hook 中只调用原生一次，
  再以当前 `vm_map` 为租约键，先显示后视频执行回调；映射函数不可用时退回 task 身份。
- `MetalTransactionExecutor.hpp`：从实际显示补丁构造 `VerifiedPatchPlan`，读回判定后
  交由共享 `SharedRegionTransaction` 完成 COW/RX/回滚。
- `ValidatedPageBackend.hpp`：把 OpenGL 页解析拆成只读 `inspectValidatedPage()` 与
  最小 `applyValidatedPage()`，仍复用 `UserOpenGLResolver::resolvePage()`，不申请 MapGate。
- `GLManifestData.hpp`：从夹具确定性生成参考页/块摘要和位置证据；运行时只做交叉核验，
  不把参考偏移当固定写入位置。
- `KernelDisplayEvidence.hpp/.cpp`：记录 GFX9/X6000FB/DCN 只读证据；热路径只做原子计数。
- `tools/dvpf/`、`tools/vpf/build_gl_evidence.py`、`tools/vpf/analyze_display_variants.py`：
  显示清单/基线/页计划/协调器/事务探针，挂载卷只读证据和有界语义审计。

Metal 路径使用计划执行器，显示与视频共享缓存事务由协调器串行化。OpenGL 页 Hook
保留参考页快速路径和当前页动态完整块匹配；内核诊断只记录状态，离线语义报告不会生成
可安装补丁。`Applied` 标记不代表 GPU 执行或显示模式已验收。

## 证据与准入

Manifest 区分精确构建证据、完整函数重定位核验证据与离线语义证据。运行时仍受原有设备、
系统、缓存、函数字节、守卫和策略条件约束；离线分析结果不会自动成为可安装补丁。
`Mixed` 或 `Unknown` 计划不可安装。守卫先于设备补丁，页面权限恢复和回滚失败均会阻止
将相应地址空间当作成功安装。

## 生成与检查

```sh
python3 tools/vpf/generate_manifest.py --check
python3 tools/vpf/validate_manifest.py
python3 tools/test_vpf_manifest.py
python3 tools/test_vpf_plan.py
python3 tools/test_vpf_baseline.py
python3 tools/test_vpf_feature_state.py
python3 tools/test_vpf_transaction.py
python3 tools/test_vpf_video_equivalence.py
python3 tools/test_vpf_lifecycle.py
python3 tools/test_vpf_semantic_variants.py
python3 tools/test_vpf_patch_audit.py

# Display
python3 tools/dvpf/generate_display_manifest.py --check
python3 tools/dvpf/freeze_display_baseline.py --check
python3 tools/test_display_manifest.py
python3 tools/test_display_plan_equivalence.py
python3 tools/test_display_capability_state.py
python3 tools/test_display_trampoline_lifecycle.py
python3 tools/test_display_cross_module_gate.py
python3 tools/test_display_transaction_executor.py
python3 tools/test_display_baseline.py
python3 tools/test_vpf_gl_page_adapter.py
python3 tools/test_vpf_gl_dynamic_variants.py
python3 tools/test_vpf_gl_mounted_evidence.py
python3 tools/test_kernel_display_evidence.py
python3 tools/test_vpf_display_semantic_variants.py
python3 tools/vpf/build_gl_evidence.py --fixtures --check
python3 tools/vpf/analyze_display_variants.py --fail-on-rejected
```

生成头必须通过 `python3 tools/vpf/generate_manifest.py --write` 更新；基线记录生成头、相关工具的哈希和合成补丁计划摘要，
可通过 `tools/vpf/freeze_baseline.py` 重新生成。DVPF 生成头只通过
`python3 tools/dvpf/generate_display_manifest.py --write` 更新。
