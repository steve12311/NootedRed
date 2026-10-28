# VPF 架构

VPF 为用户态共享缓存补丁提供清单、核验、依赖判断和事务执行能力。Video 路径使用这些组件
生成和安装补丁；Display 仍通过现有 Resolver 和准入策略生成补丁，并复用共享缓存事务执行器。
兼容边界见 [兼容矩阵](vpf-compatibility-matrix.md)，验证范围见
[验证说明](vpf-validation.md)。

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

事务按 `vm_map` 串行化；不同地址空间可并行。同线程重复进入同一地址空间时拒绝事务。
回滚失败会隔离该地址空间；账本在最终 map 销毁时清理，生命周期 Hook 的目标无法核验或安装
失败时拒绝启用 Video。守卫页的分配、验证与保留遵循现有失败处理。

`NRedVCNVPF*` 属性用于报告路径、规划和账本状态。单个地址空间的安装成功不代表所有进程
均已安装，也不代表通过实机验收。

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
```

生成头必须通过 `python3 tools/vpf/generate_manifest.py --write` 更新；基线记录生成头、相关工具的哈希和合成补丁计划摘要，
可通过 `tools/vpf/freeze_baseline.py` 重新生成。
