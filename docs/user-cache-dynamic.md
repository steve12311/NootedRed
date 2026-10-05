# Tahoe 用户态动态缓存匹配

`-NRedComputeScratch` 与 `-NRedVCN` 仍显式启用，系统入口复用作者的
`KernelVersion::majorMatches(MACOS_26)`，限 Tahoe / Darwin 25.x。设备范围保持不变：
ComputeScratch 为 NRed 原生支持的六种 Vega APU；视频为四种 VCN 2.2 APU。

公共组件按职责分两层：`UserSharedCache.hpp` 负责有读取预算的缓存元数据、映像与 Mach-O 解析；
`UserCacheResolver.hpp` 负责完整字节核验、唯一候选扫描、相对引用和绑定核验、载荷重建。
ImmediateSync、SrdShared、LegacyBlend、ComputeScratch 和视频补丁都复用这些基础能力。
`UserSurfaceSyncResolver.hpp` 保留渲染策略关系及 Metal 专属目标核验，
`UserVideoDecodeResolver.hpp` 保留视频计划；公共层不依赖任一生成数据头。
`tools/cache_resolver_data.py` 共用生成完整函数、指令／数据分界及引用描述的逻辑。

参考 UUID `31374756-89B4-34E8-A34F-56C5D57216A1` 使用生成数据的静态快路径。
其他 UUID 通过当前进程中的 dyld header、image table 与 Mach-O 解析定位映像，
再匹配补丁所在的完整原生函数或本机设备已应用的完整载荷。UUID 不作为动态路径的兼容证明。
解析器仅读取并生成计划；桥接完成所有字节与 RX 权限检查后才写入私有 COW 页。

动态路径重新计算 rel32 调用与 RIP 位移。已完整核验的目标函数允许搬迁；
其他外部目标仍要求处于原映像内的参考 RVA。dyld 合并 GOT / 调用桩按当前绑定指针
核对目标映像与 RVA，保留同一绑定的不同引用位置，不能把所有引用改到第一个 GOT 槽。
这支持相同实现的缓存重排，不能自动适配任意新函数实现、外部符号 RVA 或驱动 ABI。

ComputeScratch 同时核验已撤回 VT 跳转所在的完整原生函数，未知缓存不使用固定 VT 偏移。
生成器以 `LC_FUNCTION_STARTS` 确定该内部函数边界，使用 `LC_DATA_IN_CODE` 区分跳转表与指令。
参考文件中的链式指针按 [Apple dyld 缓存格式](https://github.com/apple-oss-distributions/dyld/blob/main/include/mach-o/dyld_cache_format.h)
的 `slide_info2` 解析；运行时核验的是当前进程里已经重定位的指针。

每次解析至多读取 32768 次、32 MiB；文本扫描不超过每个被补丁修改的映像 8 MiB，
完整候选上限为 128。重复匹配、元数据越界、读取失败、绑定不符或 rel32 溢出均拒绝。
视频及 Scratch 均支持补丁跨页，失败时回滚所有涉及页面并恢复保护。
混合策略不报告为成功，重复调用已完整应用的载荷不再次写入。

视频的 `NRedVCNUserCacheUUID0..3` 与渲染的 `NRedImmediateSyncCacheUUID0..3`
为实际 UUID 的四个原始 32 位字，不是兼容性白名单。
相应 `DynamicMatched`、`DynamicApplied`、`DynamicRejected`、`DynamicReads`
记录动态核验、完成写入并恢复 RX、拒绝和读取次数。
渲染诊断 `NRedImmediateSyncRevision=4` 用于区分含动态 Scratch 的版本。

离线回归包括六种 Scratch 设备、四种视频设备、重排映像／函数／GOT、跨页部分写入、
权限失败、幂等与撤回策略拒绝，并提供 ASan/UBSan。只读参考缓存回归还核对动态计划
与静态数据逐项一致。不同 Tahoe 缓存的实机启动、画面与持续视频播放仍需测试人员验收。
