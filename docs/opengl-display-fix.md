# OpenGL SE/CU 与条件 DualQuad 修复

`-NRedDisplayFix` 同时启用 Metal 显示组和 OpenGL 修复；不增加分项启动参数。
支持 Darwin 23/24/25 与 NRed 原生支持的六种 Vega APU。

## 行为

- `_gfx9_HwInfoInitData` 的三个有效 Vega 分支只对本机核显设置 `numShaderEngines=1`，
  再保留原生 32 位乘法计算 CU 数量。其他 PCI、未分类标志、无效修订及其余信息字段保持原样。
- `_glrAMD_GFX9_UpdateHwCbDualQuadMode` 仅在双源混合时，为目标核显设置
  `CB_COLOR_CONTROL.DISABLE_DUAL_QUAD`。普通绘制、其他 GPU 的能力判断和 PM4 提交逻辑保持原样。
- 不更改 OpenGL 家族编号、AddrLib、UBM、Metal 的拓扑或同步策略。

## 动态匹配与内存页加载

OpenGL 驱动是独立文件，不能依靠 Metal 的共享缓存加载钩子处理。
初始化只选择设备、分配诊断回调并注册原生页校验钩子，不排队执行准备任务。
OpenGL 路径不读取驱动文件、不解析 Mach-O、不保留整页计划，也不在页入路径分配堆内存。

`_cs_validate_page` / `_cs_validate_range` 先执行原函数，再检查验证结果、taint/NX、
文件偏移和当前页边界；4 KiB 页接受布尔成功值 `1` 或 XNU 整页成功位图 `0xF`，
其他验证值拒绝；只处理名称精确匹配 `AMDRadeonX5000GLDriver` 的 vnode。
完整参考页与参考文件偏移都一致时使用快速路径；否则仅在当前 4 KiB 页面内动态扫描。

SE 匹配完整的 256 字节尾部修复块，包含三个 Vega 分支、标志处理和 CU 乘法；
DualQuad 匹配完整的 142 字节函数。15/26 的 SE 原生函数跨页，但待修复尾部没有跨页。
动态路径逐字节核验整个代码块，只掩码已知 rel32 字段，核验原生 RIP 常量和提交调用的
目标 RVA，再复用公共相对位移工具重建载荷。目标 RVA 的约束与原解析器一致，
不能据此声称支持未知 ABI 或任意外部目标布局变化。

动态路径允许代码块在文件及页内移动，不依赖 UUID 或固定函数位置；
原生和已修复候选都参与页内歧义判断。只保证当前页内唯一，不声称映像全局唯一。
未知字节、相对目标不符、多个候选或代码块跨页均拒绝，不读取相邻页或相对目标内存。
因此不再提供旧整文件方案的跨页拼接能力。两个修复保持按本机 PCI 条件生效的载荷语义。

每次只保留一个不超过 256 字节的原始/替换代码块，校验及重建在写保护区间外完成。
获取写锁后再次比较原始代码块，只写本次修改范围。验证结果和磁盘文件不被修改。
写保护恢复失败时进行一次配对重试，仍失败则报告错误，不记为应用成功。
验证入口、目标页处理、代码块写入及解析函数以 `noinline` 隔开，限制页入链栈占用。

诊断只在实际命中或写入失败后通过 ONCE 回调异步发布；该回调只发布原子状态，
不执行文件 I/O 或补丁准备。重复校验已修复页面不重复写入，不重复排队同一状态。
非目标文件只检查 vnode 名称，不进行代码扫描；目标页每项修复最多扫描 4 KiB。

## 本机页面核验

2026-10-25，逐字节比对了本机 Tahoe 26.7.1（25G241）与
`Macintosh SSD` 的 Sequoia 15.8.1（24H32）。两者的 `0x8000` 和 `0xE000`
完整 4 KiB 页面分别与参考数据一致，函数位置如下：

| 系统 | HwInfoInitData | DualQuadMode |
| --- | --- | --- |
| 14 参考数据 | `0xE9DC` | `0x8B28` |
| 本机 15.8.1 | `0xDF40` | `0x8134` |
| 本机 26.7.1 | `0xDF80` | `0x819E` |

14 只有参考页面，未声称完成本机文件或实机核验。原始页面、完整函数摘要和来源固定在
`tools/fixtures/opengl-compat.json`；生成器及数据均不采用参考分支的页写入代码。
参考：[weachy/NootedRed 固定提交](https://github.com/weachy/NootedRed/tree/efe90c3ee8f942a5f3b909da90bdf10f71d9d5c8)。
验证器 ABI 依据 [Apple XNU 的 cs_validate_page / cs_validate_range](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/ubc_subr.c)。

## 验证与诊断

```sh
python3 tools/build_opengl_patch.py --check --verify-local
python3 tools/test_opengl_native.py
python3 tools/test_opengl_dynamic.py
python3 tools/test_opengl_bridge.py --sanitize
python3 tools/test_opengl_bridge.py --stack
```

实际 Release 栈预算检查使用同次构建的链接映射：设置 `LD_GENERATE_MAP_FILE=YES`
和 `LD_MAP_FILE_PATH` 后，运行 `python3 tools/test_opengl_stack.py <binary> <link-map>`。
该检查计入入口至首次调用前的栈预留及寄存器保存，不把它当作完整内核调用链峰值。

原生 CPU 回归穷举 PCI，覆盖三个家族分支、修订边界、32 位溢出、普通/双源混合和原生提交。
页内动态回归覆盖全部可容纳位置、两套载荷、rel32、每字节变异、重复候选和页末拒绝；
真实文件只读检查逐页定位并核对两处载荷。实际钩子回归覆盖验证结果、taint/NX、两套 ABI、
文件偏移、写入前竞争、恢复失败、异步诊断和显示总开关。初始化回归禁止启动时排队；
测试桩不提供文件 I/O 接口，避免重新引入文件准备路径。

`NRedOpenGLRevision=5`、`NRedOpenGLPageLocal=1` 标记当前页内匹配修订。
启动后核对 `NRedOpenGLEnabled`、`NRedOpenGLBackends`；加载 OpenGL 应用后核对
`NRedOpenGLSEApplied`、`NRedOpenGLDualQuadApplied` 和两个错误属性
`NRedOpenGLWriteRejected` / `NRedOpenGLRestoreFailed`。
`NRedOpenGLDynamicApplied=1` 表示两项均通过页内动态路径命中；完整参考页快速路径不置该标记。
旧的 `DynamicPending` / `DynamicReady` / `DynamicReads` 已移除。
`Applied` 包括已识别的同设备修复页面，不代表 GPU 画面已验收；仍需启动、OpenGL 绘制、
双源混合、Metal/OpenGL 混用及睡眠唤醒的实机回归。
