# VCN2 第一阶段安全候选

> 历史记录：第一阶段引入的编解码格式／尺寸守卫页及其生成、打包工具已整体移除。
> 用户态适配现在直接安装经完整函数核验的设备补丁；本文保留当时的范围、证据和
> 测试门禁，不再描述当前实现。

基线：`4e8e5e22c61a9c47a6dea64430cfd718056057f5`（包含共享缓存数据节重定位修复）。本阶段只修复危险编码尺寸与不支持的 H.264 输入，不开放竖屏 4K、HEVC 8K、Main10 编码或其他格式。

## 生效范围

| 系统 | 离线证据 | 运行时策略 | 实机状态 |
|---|---|---|---|
| macOS 14 / Darwin 23 | 缺少新增入口的完整函数证据 | 拒绝启用该进程的 VCN 用户态适配，`NRedVCNUserSafetyProfileMissing=1`、`NRedVCNUserRejected=6` | 未验证 |
| macOS 15 / Darwin 24 | 15.8.1，缓存 `42ec2890bc40317ebb2745142bff7f26` | 完整函数、所有重定位目标及节布局匹配后安装 | 未验证 |
| macOS 26 / Darwin 25 | 26.7.2，缓存 `21fb8708fc8e3765be8c65d612cdf049` | 同上；保留最新提交的独立数据节定位 | 未验证 |

仅允许 PCI `15E7 / 1636 / 1638 / 164C`，沿用 `-NRedVideoFix`。缓存 UUID 是诊断和生成来源记录；运行时按完整代码和类型化引用核验兼容性，不按 UUID 放行。其他主版本拒绝启用。没有这些版本和设备的实机结果，不能据此宣称整批 VCN2.2 已通过。

## 四个安全入口

| 原生入口 | 检查时机与行为 |
|---|---|
| `AvdCapability::checkCapabilityList` | 原生能力刷新成功后检查编码申请；AVC 宽至少 128，HEVC 宽至少 130，高至少 128，上限仍为 4096×2304 |
| `VAUveEncoder::configure` | 实际 `Vcn2Encoder`、白名单核显的重新配置进入处理器前检查逻辑尺寸 |
| `VAUveEncoder::EncodePicture` | 每帧检查逻辑、编码器对齐尺寸、输入纹理可见尺寸和存储尺寸，并检查包含关系 |
| `AvdAvcDecDataProcessor::SetDecodeMessageFields` | 每次从实际 SPS 检查 Profile 66/77/100、4:2:0、非独立色平面、亮度/色度 `bit_depth_minus8=0`；不参考请求的输出像素格式 |

编码各尺寸须为偶数；逻辑尺寸不因纹理补齐而被放行。未知编码申请、不合法或溢出尺寸返回原生内部错误 `5`，原有 Profile、上限、资源分配与错误传播继续执行。H.264 SPS 每帧重新检查，参数集改变不能复用之前的通过结果。CPU 回归同时执行真实的上游 `Process`，验证拒绝后不会调用最终命令处理入口；实际 VideoToolbox 错误映射仍待实机验证。

## 动态匹配与安装事务

生成命令（生成头文件不得手工修改）：

```sh
python3 tools/build_video_decode_patch.py --safety-major 25 --check-safety
python3 tools/build_video_decode_patch.py --safety-major 24 --safety-cache /path/to/dyld --check-safety
```

每个系统独立捕获完整函数和上下文。`Vcn2Encoder` 的 C1/C2 构造函数同型，使用 C1 与相邻完整 `init` 的联合上下文区分；不能只按短前缀或被掩掉的 rel32 判断。数据引用使用来源版本专属的段名、节名和节内偏移，最终仍核验所有引用。入口跳转中的地址仅在初扫时按类型暂时忽略，随后必须与完整守卫页、所有实际绑定地址逐字节相等。

原有补丁与守卫共用 32,768 次 / 32 MiB 读取预算。两个真实缓存的联合解析均未超预算。原生入口只覆盖完整指令边界内的 14/17/20/16 字节；重放入口没有 RIP 引用或控制流。原生消息打包代码不改写。守卫代码在一页进程私有内存中生成，写入并读回后，将当前和最大权限都下调为 RX。

先完整核验，再安装全部安全入口，最后安装设备适配；失败按相反顺序回滚。若设备补丁无法完全回滚，保留全部已安装守卫和其 RX 页，避免设备适配残留时移除保护。仅有部分守卫、部分设备补丁、错误绑定、非 RX 页或篡改内容均拒绝认定为安装完成。恢复失败属于明确异常，不能视为安装成功。

共享区域原始系统调用在锁外执行；补丁安装在其返回后串行进行。安装状态只在完整事务或完整重复核验成功后记录。

## 安装证据与实机测试门禁

- `NRedVCNEncodeGuardRevision=1`、`NRedVCNDecodeGuardRevision=1`：某进程完成完整安装或重复核验。
- `NRedVCNGuardedEncoderPID` / `NRedVCNGuardedDecoderPID`：相应实际 XPC 服务完成核验时的 PID。
- `NRedVCNUserGuardRejected=1`：守卫无法完整匹配。
- `NRedVCNUserGuardAllocationRejected=1`：私有页分配、写入、权限或读回核验失败。
- `NRedVCNGuardRollbackRetainedPage=1`：回滚不完整，保留守卫页，必须停止实机测试并保存日志。

这些值证明安装状态，不能证明编解码结果正确。`tools/run_video_encode_test.py --hardware` 在启动硬件用例前核对白名单、两个守卫版本和仍存活的实际编码服务 PID；缺少证据直接停止。测试保存阶段日志并监视新增 GPU 重置和 WindowServer 看门狗事件；首次异常或超时停止所有后续用例。杀掉测试进程不等于取消 GPU 工作。本候选打包过程不运行硬件用例。

安装后先用正常尺寸建立服务，再确认上述安装证据，随后才能测试 64×64 等非法输入。顺序为创建/重配拒绝、正常 AVC/HEVC 编码、重复创建销毁、长时间/并发、睡眠唤醒；解码先跑合法 8 位输入，再测试 High 10 初始输入和参数集切换。逐帧比较软件参考，编码回读需检查实际 Profile、位深、帧数、时间戳及亮度/色度误差。仅离线通过不构成这些实机项目的完成证据。

## 构建、候选与回退

### 栈预算

使用生产编译选项的 `-fstack-usage` 与优化后 LLVM IR 统计直接调用链，并补计守卫读取回调及两层系统调用钩子。单帧预算为 2 KiB，入口为 512 B，自身可见调用链预算为 8 KiB。XNU 的 x86 发布内核默认栈为 16 KiB（[定义](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/mach/i386/vm_param.h)）；插件的 Debug 构建并不会扩大运行内核的栈。

视频事务帧由 3,336 B 降到 616 B；补丁表、页面表、读取上下文和守卫临时前缀放入可失败检查的堆缓冲。显示 Scratch 的十映像表也复用现有堆工作区。大缓冲扫描、映像解析及完整计划解析禁止内联，防止 Release 再合并栈帧。

| 配置 | 最大单帧 | 两钩子下最坏可见调用链 | 最大入口帧 |
|---|---:|---:|---:|
| Debug | 1,816 B | 6,768 B | 88 B |
| Research Release | 1,592 B | 5,088 B | 72 B |
| Release | 1,592 B | 5,088 B | 72 B |

复测命令：`python3 tools/test_vcn_stack.py`。工具发现超预算、递归或动态栈帧即失败，不扩大阈值。上述上界不含系统调用、VM、IOKit、锁和 `copyin/copyout` 内部栈；为其保留余量，仍需实机核验。详细编译命令及逐函数路径在候选的栈报告中。

交付包包含 Debug、Research Release、Release 的 kext / 调试符号及原项目归档、源代码差异和新增源文件、逐阶段测试/构建/分析日志、补丁清单与 SHA-256。扩展功能位必须为 `0`。

本次不修改 EFI、持久化启动参数或系统配置。安装及重启另行执行；安装前保留当前可启动 EFI 和旧 `NootedRed.kext`。若出现异常，通过备用启动项恢复旧 kext，或移除 `-NRedVideoFix` 关闭整条可选视频适配；不要在已经报告 GPU 重置后自动重试危险输入。因 macOS 14 安全守卫缺乏证据，该系统的视频适配会比旧版本收紧为拒绝状态。
