# Cezanne VCN 解码接入

## 范围

实验性接入仅支持 PCI `1002:1638`、Darwin `25.6` 和生成数据指定的 dyld 缓存。
使用 `-NRedVCN` 显式启用；保留已验证渲染基线的启动参数。
默认关闭，不扩大其他 GPU 或 macOS 版本的支持范围。

## 实现

X5000 加速器启动前请求可选加载 X6000；只有同步加载、两侧 hook 准备成功才接入
原生 VCN2 引擎、视频上下文与客户端。加载失败／延迟时不在既有实例中途启用。
插件库依赖保持不变，跨驱动虚调用按本机虚表修正，包含 GFXOFF 等待配对。

用户态补丁仅写入匹配缓存 UUID、原始指令及 RX 保护的进程私有 COW 页。
失败时回滚已写字节并恢复保护；保留原生返回值。生成头文件不手工修改。
AppleGVA 在无 Intel、仅 AMD 可用的机型回退出口选择已有的 AMD 解码器，
保留混合 GPU、已知机型、显式设备请求及无可用 GPU 的原生选择。

## 生成与验证

在当前适配的 macOS 主机执行；不匹配的驱动／缓存不得直接复用补丁：

```sh
python3 tools/build_vcn_kernel_patch.py
python3 tools/build_video_decode_patch.py
python3 tools/test_vcn_candidate.py
python3 tools/test_vcn_addr_profile.py
python3 tools/test_vcn_user_bridge.py
python3 tools/test_vcn_native_load.py
python3 tools/test_vcn_idle_calls.py
python3 tools/test_vcn_gva_choice.py
python3 tools/test_surface_sync_bridge.py
python3 tools/test_compute_vtable.py
```

中间文件位于 `build/vcn/`，子进程限时 60 秒。
另执行 Debug、Research Release、Release 的构建与静态分析，
并以 `tools/test_vcn_package_dependencies.py <构建后的 Info.plist>` 检查加载依赖。

## 实机验收与限制

正常尺寸 H.264／HEVC 的短视频已观察到硬解与软件像素摘要一致；64×64 H.264 仍失败。
Hackintool 检查曾触发 VCN0Dec 未完成与 WindowServer 看门狗；GFXOFF 调用修正已通过
离线回归，消除挂起的实机验证尚未完成，不能声明稳定或完整解码支持。

验收覆盖 Hackintool 创建／销毁解码器、至少五分钟空闲后再启动、持续播放与桌面响应。
保留有次数上限的诊断字段／日志；记录触发时间、硬解属性及 `.gpuRestart`／WindowServer 报告。
`NRedVCNGVASelectionFix=1` 表示新选择补丁已随完整用户态事务写入并恢复 RX。
Apple TV 在线观看的密钥请求及系统网络／音频 panic 需要独立验收，renderer 选择修正
尚不能证明这些故障已解决。
