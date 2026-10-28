# Display VPF 架构

Display Verified Patch Framework（DVPF）为显示补丁提供清单、核验、依赖判断、
能力状态和事务执行。它复用 VPF 的共享缓存基础设施，保留 OpenGL 页校验、Metal
共享缓存和内核显示路径各自的执行边界。

## 执行域

| 域 | 入口 | 约束 |
| --- | --- | --- |
| OpenGL | `_cs_validate_page` / `_cs_validate_range` | 仅处理已验证的当前页和完整页内代码块 |
| Metal | `_shared_region_check_np` | 按当前 `vm_map` 核验并安装共享缓存补丁 |
| Kernel Graphics / Framebuffer | `X5000`、`X6000FB`、DCN | 只读采集显示状态，不使用用户态事务 |

## 清单、计划和状态

- `DisplayManifest.hpp` / `DisplayManifestData.hpp` 描述显示域、功能组、站点和证据；
  Metal 站点复用现有 VPF 数据。
- `DisplayPlan.hpp` / `DisplayPlanAdapters.hpp` 将解析结果转换为只读计划。OpenGL
  页计划要求验证成功、`tainted=0`、`nx=0`、目标 vnode 和页内完整块。
- `DisplayDependencyPlanner.hpp` 计算组依赖和拒绝原因；`DisplayCapabilityState.hpp`
  使用共享账本提供显示组视图，不创建第二套地址空间账本。
- `ExecutableTrampolineResource.hpp` 跟踪 SubmitGuard 跳板生命周期；入口无法确认恢复时
  保留目标页。

## 共享缓存事务

当显示与视频模块都成功注册时，`UserPatchCoordinator` 在最外层调用一次原生
`_shared_region_check_np`，以当前 `vm_map` 为租约键，然后依次运行显示与视频回调。
映射函数无法解析时退回 task 身份；映射结果为空时跳过。相同租约键串行，不同键可并行；
单模块或协调器安装失败时保留已有路径，重入与容量不足按现有拒绝策略处理。

Metal 补丁由 `MetalTransactionExecutor` 从核验后的站点构造 `VerifiedPatchPlan`，
读回判定后交给共享事务执行器处理 COW、写入、RX 恢复和失败回滚。SubmitGuard 先于
普通渲染组执行，并保留独立的失败和跳板页生命周期。

## OpenGL 页面处理

`ValidatedPageBackend` 把页面处理分为只读 `inspectValidatedPage()` 与最小范围的
`applyValidatedPage()`。写入前再次验证页面字节；原生页 Hook、参考页快速路径与同页
动态完整块匹配保持有效。该路径不读取文件、不申请共享缓存租约，也不处理跨页事务。

`GLManifestData.hpp` 由夹具生成，保存参考页和块的摘要与位置证据；它用于交叉核验，
不把参考偏移当作固定写入位置。未知字节、歧义候选、无效相对目标或跨页块仍被拒绝。

## 内核证据与离线分析

`KernelDisplayEvidence` 记录 GFX9、Framebuffer、scanout 和 Flip 的只读状态；热路径
使用有界原子计数，属性在初始化、状态变化或长等待时发布。这些记录不改变原有显示和
Flip 行为，也不能单独证明 GPU 执行成功。属性说明见
[内核显示证据](kernel-display-evidence.md)。

`tools/dvpf/` 提供清单、基线、计划比较和事务探针；`tools/vpf/` 提供 OpenGL 证据与
离线语义审计。离线报告不生成可安装 Manifest，也不放宽运行时准入。

## 验证边界

显示基线记录补丁字节、设备范围、源文件哈希和测试命令，见
[显示基线](dvpf-baseline.md)。用户态夹具、只读文件检查和离线报告不能替代内核 Hook、
WindowServer、Fence、Compute、显示模式或 GPU 绘制的实机验证。`Applied` 属性只表示
补丁路径的观察结果，不代表图形工作负载已经通过验收。
