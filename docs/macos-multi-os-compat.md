# Darwin 23 / 24 / 25 统一修复

三个系统共用启动入口、完整函数及引用核验、VPF 依赖规划、私有 COW 页面事务、
失败回滚、RX 权限恢复、FVRC 位置缓存和按 `vm_map` 串行的显示／视频协调器。
系统差异只用于选择对应数据和已核实的 ABI，不共享固定函数偏移。

| 系统 | 参考缓存 | 显示数据 | 视频数据 |
| --- | --- | --- | --- |
| macOS 14.8.9 / 23J631 | `4C1441FD-8A54-3DC2-863D-49110B946A25` | `UserSonomaSurfaceData` | `UserSonomaVideoData` |
| macOS 15.8.1 / 24H32 | `42EC2890-BC40-317E-BB27-45142BFF7F26` | `UserSequoiaSurfaceData` | `UserSequoiaVideoData` |
| macOS 26.7.2 | `21FB8708-FC8E-3765-BE8C-65D612CDF049` | `UserSurfaceSyncData` / `UserSurfaceConstructorData` | `UserVideoDecodeData` |

Darwin 23 的参考数据来自 macOS 14.8.9 / 23J631 的只读系统缓存镜像。
原生函数地址、长度、SHA-256 和上下文记录在 `tools/fixtures/sonoma-compat.json`。
UUID 用于溯源、诊断与 FVRC 身份核对；参考 UUID 也必须通过完整函数和引用核验。
未知函数实现、绑定目标、设备或 ABI 继续拒绝，不按主版本直接放行。

## 功能与参数

- `-NRedDisplayFix`：三系统统一启用 SRD、LegacyBlend、ComputeScratch、SubmitGuard 及现有 OpenGL 修复。
  保留原生同步策略，不启用 ImmediateSync。`NRedDisplayFixVersion=3` 标识此组合。
- `-NRedVideoFix`：四种 VCN 2.2 APU 共用设备准入、23 项视频补丁和安装生命周期。
  FairPlay 客户端按签名权限选择 18 项 AMD 解码子集，排除 AppleGVA 与编码项；
  `NRedDRMVideoRevision=5` 标识 Darwin 23 已接入同一动态路径。
- `NRedECFix=1`：沿用现有 EC 背光服务；完整说明见 [启动参数](boot-arguments.md)。

Metal 仍只接受 `15D8 / 15DD / 15E7 / 1636 / 1638 / 164C`；
VCN 仍只接受 `15E7 / 1636 / 1638 / 164C`，不开放 VCN1。
OpenGL 仍须匹配已验证页内的完整块。受保护视频黑屏和固件证书问题没有解决，HDR 候选未集成。

## Darwin 23 的数据差异

Metal 初始化为 218 字节，24/25 为 215 字节；生成器复用载荷和引用提取逻辑，按原生长度补齐。
Scratch 为 456 字节，布局与 24 相同，但在参考缓存中跨页；公共事务收集并保护两页。
视频地址库与 PSP 工厂使用 23 专属比较位置；编码参数函数为 148 字节，
复用经核实的 138 字节载荷并补齐，其余编码消费者保留完整原生上下文。
三系统的提交方法均以完整 47 字节及引用动态定位，重复安装核对守卫全部字节和返回地址。

`import_legacy_compat.py` 生成 `VCNLegacyKernelData.hpp`；历史来源夹具用于 ABI 与语义回归。

## 生成与验证

`CACHE23` 指向只读挂载的 Darwin 23 镜像中 `System/Library/dyld`，不修改系统缓存或 EFI：

```sh
python3 tools/build_sequoia_surface.py --major 23 --cache "$CACHE23" --check
python3 tools/build_video_decode_patch.py --sonoma-cache "$CACHE23" --check
python3 tools/import_legacy_compat.py --check
python3 tools/test_cache_resolver_reference.py --sonoma-cache "$CACHE23"
python3 tools/test_surface_sync_patch.py --sonoma
python3 tools/test_compute_scratch_patch.py --sonoma-cache "$CACHE23"
python3 tools/test_video_encode_patch.py --sonoma-cache "$CACHE23"
python3 tools/test_video_encode_main10_patch.py
python3 tools/test_multi_os_compat.py
python3 tools/test_display_runtime_bridge.py --sanitize
python3 tools/test_compute_cache_dynamic.py --sonoma --sanitize
python3 tools/test_video_cache_dynamic.py --sonoma --sanitize
python3 tools/vpf/generate_manifest.py --check
python3 tools/dvpf/generate_display_manifest.py --check
```

动态缓存测试省略系统选项运行 25，使用 `--sequoia` 运行 24，使用 `--sonoma` 运行 23。
各系统覆盖未知／参考 UUID、六种 Metal／四种视频设备、函数和 GOT 重排、完整字节与引用拒绝、
部分写入、跨页权限失败、事务回滚、重复安装和 FairPlay 子集。
原生指令 CPU 回归验证配置位、设备限制、Scratch 拓扑、纹理 pitch 和反馈布局。
这些检查不提交 GPU 命令，不能代替三系统的实机启动与持续运行验收。

实机验收需记录 GPU 型号、完整系统构建号、缓存 UUID、加载 kext UUID 和启动参数。
分别核对 WindowServer 显示／提交守卫与 VCN 的 `Applied`、ABIValidated、VPF 状态，
验证菜单混合、Metal 渲染、持续解码／编码、空闲后重新播放和睡眠唤醒，保存首次异常的
`.gpuRestart`／`.panic`。离线检查不表示三系统硬件功能已经验证。
