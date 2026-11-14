# 进程过滤与 FVRC 复用

共享缓存补丁在每次 exec 的 `_shared_region_check_np` 返回后执行。本页说明两项降低 exec 开销的机制：
仅跳过明确列出的基础系统命令，以及让 FVRC 在 Tahoe 的真实映射上命中。
两者都不改变补丁内容、完整函数与引用核验或写入事务，详见 [用户态动态缓存匹配](user-cache-dynamic.md)。

## 进程过滤（UserProcessFilter）

协调器及两个模块的非协调 Hook 在解析前调用 `UserPatchCoordinator::processAllowed()`。
过滤器通过 `proc_getexecutablevnode` 取得刚完成 exec 的映像，以 `vn_rdwr(IO_NOAUTH)` 读取
Mach-O 头与加载命令（头部 4 KiB，命令最多 32 KiB；支持 fat 中的 x86_64 切片），仅在全部条件成立时跳过：

- 路径精确匹配 81 个基础系统命令，覆盖文件操作、文本处理、压缩、目录查询等。
  例如 `cp`、`rm`、`mkdir`、`awk`、`sed`、`sort`、`tar`、`xargs`；完整名单见
  [UserProcessFilter.hpp](../NootedRed/UserProcessFilter.hpp) 的 `Paths`；
- 直接依赖也须精确匹配 `Libraries` 中的底层库。命令在名单中但出现未知依赖时仍补丁；
  不对 `/usr/lib/*` 或框架目录进行整体放行。

未知程序、解释器、工具链、应用及 WindowServer 保持补丁。加载命令只能提供额外约束，
不能单独证明程序不会通过 `dlopen` 或插件使用 GPU。路径含 `..`、额外后缀或目录前缀也不会匹配。
格式异常、读取失败、找不到符号或缓冲分配失败一律回到补丁。

| 名称 | 说明 |
| --- | --- |
| `-NRedPatchAllProcs` | 启动参数：关闭过滤，所有进程照常补丁 |
| `NRedProcessFilterRevision` | 实现版本，当前为 3 |
| `NRedProcessFilterEnabled=1` | 过滤已启用 |
| `NRedProcessFilterDisabled` | `1` 由启动参数关闭；`2` 内核缺少 `proc_getexecutablevnode` |
| `NRedProcessFilterChecked` | 路径符合条件并读取了加载命令的 exec 次数 |
| `NRedProcessFilterSkipped` | 跳过补丁的 exec 次数 |
| `NRedProcessFilterFormat` | 因格式问题保守补丁的次数 |

计数在前 16 次逐次发布，之后每 256 次发布一次，避免每次 exec 都持有注册表锁。

## 重复回调与拒绝缓存

同一 `vm_map` 的重复回调保留 proof 快捷路径。只有缓存基址、缓存头（含 UUID）以及
两侧全部补丁站点的完整替换字节均匹配时才跳过完整搜索；读取失败或任一字节变化都会回退。
每个参与者最多保存 32 个站点，每个站点最多 512 字节，与验证计划上限一致。
快照分配在堆上，使用引用计数保护锁外读取；淘汰、map 销毁与事务结束时释放。分配失败或超限保持 Pending。
全局最多保留 64 个 map 的两侧证明；核验缓冲只占 512 字节内核栈。
`NRedPatchCoordinatorRevision=3` 标识此行为；`NRedPatchCoordinatorSkippedRuns` 继续记录快捷命中次数。

拒绝表只记录完整读取镜像表后确认缺少目标镜像的 UUID。读取失败、预算耗尽、镜像表不完整、
函数或引用核验不匹配都不会增加拒绝计数。两次确认、每 64 次复查及成功接受后的保护继续有效。

## FVRC 映射身份

FVRC 只在整个映像 `__TEXT` 由同一只读共享对象提供时才把位置标为可复用
（`ReusableImmutableIdentity`）。Tahoe 的共享缓存文本在机器级子映射中报告为 `SM_COW`，
原始对象自带 shadow 链（实测 `shadow_depth=2`，各进程 `object_id` 相同）；进程写入的页会换成新的顶层对象。
因此判定条件为：

- `SM_TRUESHARED` 且 shadow 深度为 0，或 `SM_COW` 且 shadow 深度不超过 4；
- 叶子条目不可写，`object_id` 非 0；
- 范围内所有叶子的 `object_id` 与 shadow 深度一致，否则视为已有私有页（Mixed）。

不同子缓存文件（`.01`–`.06`）是不同的 VM 对象；身份按映像记录，单个映像不会跨子缓存。

## 弱证明不得禁用强证明

已补丁进程再次进入解析时，映射不干净且读到的是已改写的变体，只能发布 `HintOnly`。
旧逻辑把这类结果与已有条目比较布局（含变体），不一致即标为冲突并永久禁用该键，
导致实测每次 exec 只有约 2/5 组命中。现在：

- 已有 `Reusable` 条目时，`HintOnly` 结果记为 Duplicate，不再比较布局；
- 已有 `HintOnly` 条目时，`Reusable` 结果直接取代它，即使布局不同；
- 两个 `Reusable` 结果布局或映射身份不一致时仍禁用该键。

## 诊断

| 属性 | 说明 |
| --- | --- |
| `NRedFVRCDisplayHot` / `SubmitHot` / `DecodeHot` | **最近一次**解析是否命中；已补丁进程的重复解析会把它们刷回 0 |
| `NRedFVRCHotTotal` | 累计命中组数（前 16 次逐次，之后每 256 次发布） |
| `NRedFVRCMappingReject` | 最近一次映射检查：低 8 位原因码，8–15 位 share mode，16–23 位 shadow 深度 |

`NRedFVRCMappingReject` 原因码：0 通过，1 参数，2 区域过多，3 查询失败，4 子映射层级，
5 share mode，6 shadow 深度，7 可写，8 对象为 0，9 对象或 shadow 不一致，10 空范围。
Tahoe 正常值为 `0x20100`（通过、`SM_COW`、shadow 2）。

判断命中率时应以 `NRedFVRCHotTotal` 的增量除以补丁进程 exec 次数，每次 exec 共 5 组
（Submit、Display Core / Constructor / Scratch、VideoDecode）。

## 原 PR 实测（Renoir 0x1636，macOS 26.7.1）

| 场景 | 修改前 | 修改后 |
| --- | --- | --- |
| `/usr/bin/true`（跳过） | 43.8 ms | 5.3 ms |
| `/bin/ls /`（跳过） | 42.9 ms | 5.6 ms |
| 空程序（补丁） | ~42 ms | ~31 ms |
| FVRC 命中组数 / exec | ~2 | 5 |

以下历史测量来自原 PR，修复版采用 81 个命令的精确白名单和全站点 proof 核验，性能需重新测量。

用户态复现中，冷解析约 24 ms、全部命中约 4–5 ms。补丁进程剩余开销主要来自页写入，
以及每个已补丁进程一次冷的重复解析（约 10 ms，读取 5232 / 3391 次）。

## 回归

- `tools/test_display_coordinator_kext.py`：跨模块协调、全站点 proof、第二站点及长补丁尾部变化、UUID 变化、读取/分配失败和资源释放。
- `tools/test_display_cross_module_gate.py --sanitize`：证明快照的并发发布、锁外读取、淘汰及 map 清理。
- `tools/test_rejected_caches.py`、`tools/test_fvrc_video.py`：瞬时失败恢复、代码不匹配、缺少镜像及拒绝表并发。
- `tools/test_process_filter.py`：白名单、工具链动态加载反例、合成 Mach-O 边界及本机系统命令分类；`--live` 统计运行中进程，
  并要求应用与 WindowServer 保持补丁。
- `tools/test_fvrc_mapping_probe.py`：真实 Mach ABI 下的 COW / shadow / 私有页判定。
- `tools/test_fvrc_registry.py`：冲突禁用、证明升级，以及 `HintOnly` 不得禁用 `Reusable`。
- `tools/bench_exec_latency.py`：对比跳过与补丁进程的 exec 延迟。
