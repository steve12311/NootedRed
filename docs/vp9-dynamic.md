# VP9 动态适配与验收

实验功能默认关闭，启动参数为 `-NRedVideoFix -NRedVP9Probe -NRedVP9`。
Darwin 23/24/25 统一进入动态解析，使用本机 macOS 26.7.3 / 25G342、
共享缓存 `5a3da575-f02e-33dc-9b14-f663050011e6` 重新生成参考配置。
UUID 仅用于诊断和已核验计划的身份核对；其他 UUID 仍须通过全部代码及 ABI 核验。

## 运行条件

- 沿用四种 VCN 2.2 APU 的设备准入和原有视频补丁；不开放 VCN1 或 Darwin 27。
- 匹配 22 个完整函数及全部外部引用、数据节，核对后端使用的构造函数和虚函数槽位。
  函数或数据搬移时重建 rel32；对象布局、槽位或实现不同则拒绝，不按系统主版本直接放行。
- VP9 安装工作区显式清零，使用现有 COW、读回核验、回滚和 RX 恢复事务。
  写入前权限失败且已恢复时允许重试；写入失败后停止本次启动的 VP9 写入。
- 32 个 VP9 站点单独加入协调器完成证明；未完成、字节变化或读取失败不能跳过安装检查。
- 后端安装成功后才发布 GPU 的 VP9 能力。核验或事务失败会撤回能力键；
  软件回退是否即时发生仍取决于客户端的解码器枚举和会话重建行为。
- 支持 profile 0、NV12、最大 4096×4096。profile 2/HDR、官方全部向量、长时间播放和睡眠唤醒仍待实机验收。

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

动态夹具覆盖未知 UUID、函数/数据节重排、ABI 和完整字节拒绝，并经过 Darwin 23/24/25 分派。
这证明共用匹配路径的行为，不能证明未提供参考缓存的 macOS 14/15 原生驱动一定匹配。
当前版本仅实际读取本机 Darwin 25 缓存；Darwin 23/24 若实现不同，将保持软件路径，
需要取得相应只读缓存后增加经过核验的语义配置，不把拒绝误报为功能适配完成。
既有三个系统的视频补丁另由 `test_video_cache_dynamic.py --sonoma/--sequoia` 及默认配置回归。

## 安装验收

记录 GPU 型号、完整系统构建号、缓存 UUID、加载 kext UUID 和启动参数。
启动 GPU 客户端后检查 `NRedVP9Applied`／`NRedVP9Already` 及 `NRedVP9Probe=1`；
只有 `NRedVP9State=2` 不能证明安装完成。
先逐帧比较短 profile 0 片段，再检查浏览器、并行 H.264/HEVC、空闲恢复及睡眠唤醒。
保留首次 `.panic`／`.gpuRestart` 及 `NRedVP9*` 属性；离线测试与构建结果不代替这些实机结果。
