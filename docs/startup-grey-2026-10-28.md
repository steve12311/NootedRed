# 启动灰屏调查与地址空间事务锁修复

## 本次启动证据

启动会话为 `A2AF4E9E-6F43-450E-B551-CB46CD1EFE19`，启动时间
2026-10-28 00:57:53（UTC+8），macOS 26.7.2 / 25G332。
实际加载的 NootedRed UUID 为 `23DDFB18-BFB7-3F4A-AEFE-0BF2B5902328`，
对应 `8bdc4db` 的 Release 构建。

以下记录均按本次启动筛选，不混用早期日志中校时前或其他启动的事件：

| 时间（UTC+8） | 事件 |
|---|---|
| 00:58:15.827 | WindowServer 启用 Metal compositor |
| 00:58:29.440 | loginwindow 开始等待 Dock/Desktop 就绪 |
| 00:58:31.949 | WindowServer 首次报告 `timed out fence` |
| 00:58:34.520–00:58:50.798 | 壁纸扩展连接创建耗时 16.278 秒 |
| 00:58:44.441 | 登录转场等待超时，强制开始转场 |
| 00:58:51.505 | Dock 才发布 `com.apple.desktop.ready` |
| 00:58:52.457 | 登录完成 |
| 00:58:53.080–00:59:08.730 | 继续出现渲染 fence 和同步超时 |

WindowServer 的显示补丁和 SubmitGuard 安装标记均为成功，读取次数符合优化后的路径。
本次启动未发现新的 `.gpuRestart` 或 WindowServer watchdog 报告，当前 AMD
`recoveryCount=0`。安装标记和没有复位报告均不能证明 GPU 执行完全正常。

## 可复现的阻塞问题

VCN 共享缓存钩子原先在全局 `guardLock` 内执行整个读取、动态解析、守卫核验和
COW 安装事务。锁覆盖所有进程；一个进程在读取缓存页时变慢，会阻止其他独立进程
进入补丁阶段。前一轮单进程 CPU 和读取次数测量没有覆盖这种启动并发排队。

`tools/test_vcn_task_gate.py` 直接编译真实钩子，用可控的慢事务复现：
旧钩子中，A 的扫描未返回时，独立地址空间 B 的补丁也无法开始。
这确认了跨进程排队问题。现有日志没有各钩子的耗时，尚不能把全部灰屏或每一次
渲染 fence 超时都归因于这把锁；候选包仍需要启动对照测试。

## 修复

`UserPatchMapGate` 以实际 `vm_map` 为事务身份，仅串行化同一地址空间。
即使两个 task 共用同一地址空间，也不能同时修改其补丁页。

全局 IOLock 只保护 64 项活动地址空间注册表的短操作，不再覆盖扫描、用户页缺页、
分配或写入。重复地址空间和注册表满时使用 `IOLockSleep` 释放锁等待；事务结束后
唤醒对应等待者。注册表无逐请求堆分配，Lease 自动释放占用，原始系统调用仍在
申请事务资格前执行。

同一地址空间内仍保留完整匹配、设备白名单、安全守卫先安装、COW/RX 恢复和失败回滚。
缺少 map 的错误路径保留原诊断与拒绝行为，不利用并行化跳过安全检查。

新标记：

- `NRedVCNUserMapGateRevision=1`：启用了按地址空间串行化的钩子。
- `NRedVCNUserMapGatePeak`：实际同时进入补丁事务的地址空间数峰值；仅峰值增大时发布，
  最多更新 64 次，不在每次调用中写 IORegistry。

这些标记只说明安装与事务调度，不代替灰屏和 GPU 执行验收。

## 回归与测试方法

并发测试覆盖独立地址空间前进、不同 task 共用 map 时的互斥、注册表满与唤醒、
1,600 次并发 Lease、事务资格释放和原始系统调用返回值；使用 ASan/UBSan 与
ThreadSanitizer 分别执行。另复查既有事务故障注入、旧系统拒绝路径和栈预算。
具体构建、分析和栈测量结果随候选包中的验证日志提供。

Debug、Research Release、Release 构建与静态分析均通过；Darwin 24/25 分别完成
45 组 ASan/UBSan 原生事务桥接检查。Debug 可见调用链上界为 6,352 B，两个 Release
配置均为 4,200 B；最大单帧分别为 1,816 B / 1,576 B，均低于原预算。
内核 VM、IOKit、锁和 copyin/copyout 的内部栈未包含在这些静态测量内。

```sh
python3 tools/test_vcn_task_gate.py --baseline-ref 8bdc4db --expect-global-stall
python3 tools/test_vcn_task_gate.py
python3 tools/test_vcn_task_gate.py --sanitize thread
python3 tools/test_shared_region_hooks.py
python3 tools/test_vcn_stack.py
```

候选包不自动安装或重启，继续使用现有参数。保留先前可启动的 EFI/kext 后替换测试，
登录后核对：

```sh
ioreg -lw0 -r -n IGPU | rg 'NRedVCNUserMapGate|NRed.*WindowServer|NRedVCN.*GuardRevision'
log show --last boot --style compact --predicate 'process == "WindowServer" AND eventMessage CONTAINS[c] "timed out"'
```

记录灰屏持续时间以及是否还有相同 fence 超时。若仍异常，恢复先前 kext 并重启；
不要通过关闭硬件安全守卫来测试危险编码输入。
