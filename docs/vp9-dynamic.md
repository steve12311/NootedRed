# VP9 动态适配与验收

启用 `-NRedVideoFix` 后，支持的 VCN 2.2 核显默认启用 VP9 硬解。
Darwin 23/24/25 使用各自的 23J631、24H241、25G342 共享缓存生成参考配置，并共用动态解析。
UUID 仅用于诊断和已核验计划的身份核对；其他 UUID 仍须通过全部代码及 ABI 核验。

## 运行条件

- 沿用四种 VCN 2.2 APU 的设备准入和原有视频补丁；不开放 VCN1 或 Darwin 27。
- 匹配 23 或 24 个完整函数及全部外部引用、数据节，核对后端使用的构造函数和虚函数槽位。
  函数或数据搬移时重建 rel32；对象布局、槽位或实现不同则拒绝，不按系统主版本直接放行。
- VP9 安装工作区显式清零，使用现有 COW、读回核验、回滚和 RX 恢复事务。
  写入前权限失败且已恢复时允许重试；写入失败后停止本次启动的 VP9 写入。
- 29 至 31 个站点（含 VideoToolbox 筛选入口）加入同一安装事务和协调器完成证明；
  未完成、字节变化或读取失败不能跳过安装检查。
- 后端安装成功后才发布 GPU 的 VP9 能力。核验或事务失败会撤回能力键；
  软件回退是否即时发生仍取决于客户端的解码器枚举和会话重建行为。
- 支持 4:2:0 Profile 0（8 位 NV12）和 Profile 2（10 位 P010），最大 4096×4096。12 位码流会被拒绝。

## 本地验证

`tools/build_vp9_decode_patch.py --check` 使用当前参考缓存重现生成数据；
其他参考缓存可通过 `--cache` 和 `--uuid` 指定。
`tools/test_vp9_resolver_live.py` 只读当前缓存，验证原生解析、已补丁函数匹配及原视频解析器共存。

```sh
python3 tools/test_vp9_parser.py
python3 tools/test_vp9_dynamic.py
python3 tools/test_video_cache_dynamic.py --vp9 --sanitize
python3 tools/test_vp9_backend.py
python3 tools/test_vp9_message.py
python3 tools/test_vp9_probe.py
python3 tools/test_vcn_native_load.py
python3 tools/test_display_coordinator_kext.py
python3 tools/test_vp9_compare.py
```

动态夹具覆盖未知 UUID、函数及数据节重排、ABI 和完整字节拒绝。三版参考缓存也用于验证原生解析、
已补丁函数匹配，以及与基础视频补丁共存。其他缓存须通过运行时完整核验。

## 安装验收

记录 GPU 型号、完整系统构建号、缓存 UUID、加载 kext UUID 和启动参数。
启动 GPU 客户端后检查 `NRedVP9Applied`／`NRedVP9Already` 及 `NRedVP9Probe=1`；
只有 `NRedVP9State=2` 不能证明安装完成。
用 `tools/compare_vp9_decode.py` 逐帧比较 Profile 0 和 10 位 Profile 2 的亮度及色度，再检查浏览器 HDR 播放、
并行 H.264/HEVC、空闲恢复及睡眠唤醒。
保留首次 `.panic`／`.gpuRestart` 及 `NRedVP9*` 属性；离线测试与构建结果不代替这些实机结果。

## VideoToolbox 能力与会话选择

本机混合显卡环境中，首次 VP9 能力查询加载显卡组件后，另一个动态插件注册了
`org.nvenc.videodecoder.vp9.vp09` 和 `org.nvenc.videodecoder.av1.av01`，评级为 1100。
这些候选声明 `VTIsHardwareAccelerated=1`、`VTAllowSandboxedDecode=0`。
原生查询/创建模式 0/1 会接纳它们；AV1 因此从未初始化时的不可用变成可用。
Chrome 的三份故障报告均停在 `CVPixelBufferGetIOSurface` 返回空值后的断言，
另有 AMD VCN0Dec 队列超时及失效 GPU 映射报告。

修复在通用 VideoToolbox 候选回调中，将 `vp09`/`av01` 的已知模式 0/1
规范到原生模式 2 的准入规则。查询与会话创建使用同一规则；不匹配执行环境的
高优先级硬解项不能覆盖 AMD 的 VP9。创建方式由原调用方继续决定；模式 2、未知模式、
其他 FourCC（含 `qvp9`）及原生显式解码器选择逻辑保持原样。
没有按进程名称或插件厂商 ID 分支，也不修改 Chrome 或系统框架文件。
该回调完整函数与全部相对引用必须匹配，才会连同 VP9 后端写入进程的私有 COW 页。

安装成功后应出现 `NRedVTDecoderAdmissionRevision=1`。单站点上限按 VPF 的 512 字节
使用独立保存行；计划仍限制为 32 个站点，本配置实际占用 29 个站点、10 页。

```sh
python3 tools/test_vt_decoder_admission.py
python3 tools/test_vt_decoder_admission.py --live
```

默认测试离线执行 72,000 组原生/替换指令对照，无需匹配宿主缓存。
`--live` 仅修改测试进程私有代码页，比较三个原生模式和五种编码的真实选择结果，
检查 AV1 强制硬解创建返回 `-12906`，并验证短 VP9 片段仍硬解且每帧提供 IOSurface。
它需要匹配参考 ABI 的本机缓存，以及带 libvpx-vp9/libsvtav1 的 ffmpeg。
这些结果不等同于浏览器长时间播放、混合负载或睡眠唤醒验收。
