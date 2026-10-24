# FairPlay 客户端的 PSP 兼容策略

## 行为与边界

`-NRedVideoFix` 在 macOS 14/15/26 / Darwin 23/24/25、受支持的 VCN 2.2 APU 上，默认
为所有持有 `com.apple.private.fpsd.client=true` 签名权限的客户端采用同一策略。
不使用 Music、TV 或辅助服务的进程名判断是否启用；仅有相同进程名而没有该权限也不会进入此路径。
不再使用 `-NRedDRMVideo` 单独开关；缺少 `-NRedVideoFix` 时不注册桥接。

- 14 选择 13 项 AMD 驱动侧解码补丁，掩码为 `0x1FFF`；15/26 各选择 18 项，
  掩码分别为 `0x7FE1FE` / `0x7F87FE`。AppleGVA 与编码代码保持原始字节。
  15/26 包含 swizzle 消费者修正；pitch 属于编码路径，仅普通客户端应用。
- 原生 `VACPFactory::createCP` 的 PSP family 范围由 1–4 扩展到 1–5，承接 APU 识别所用的 family 5。
  family 0 继续走 SAMU，其他值继续沿用原生分支；保留对象初始化失败清理及 KE 原始错误码。
- 14/15 的工厂原始函数来自既有参考页面，分别为 182/183 字节；
  `tools/legacy_cp_factory.py` 核验完整 SHA256，再只将比较立即数 `4` 改为 `5`。
  工厂补丁与其他 AMD 解码项共用完整页面核验和 COW 回滚事务。
- 固定缓存与动态重定位均检查原生代码；未选项不符合原始字节、混合补丁或不匹配的缓存会拒绝写入。
  写入失败回滚，完成后恢复页面权限。
- 不修改 FP 固件、证书、密钥、可信平台选择字段、认证结果或 AppleGVA 持久化偏好。

2026-10-22 至 2026-10-23，Ryzen 7 5800H / Cezanne `1002:1638`、macOS 26.7.1（25G241）、
PSPFactory-v2 的用户实测：普通 Apple Music 音乐正常，受保护 MV 有连续声音、进度前进、画面全黑。
探针仍报告 `getCertASMF=false`、IOKit `0xE00002BC`，内核 FP 状态 `9` / services `2`。
该固件的证书平台选择未接受查询字段，视频保护链尚未可用。

统一权限策略保留了上述 26 Music 路径的补丁字节和 AppleGVA 排除规则；
**音频成功是既有实测结果，不等于证明 PSP 补丁单独修复了音频，也不等于 Apple TV 已实机通过。**
本版仍需重启后的 Music／TV 回归，不能把补丁安装成功当作受保护视频播放成功。

## 诊断与播放回归

`NRedDRMVideoRevision=4` 表示三系统统一权限策略。
14 为 `PatchCount=13`、`Mask=8191`；15 为 `PatchCount=18`、`Mask=8380926`；
26 为 `PatchCount=18`、`Mask=8357886`。
`State=1` 表示进入有效缓存检查，`2` 表示写入并恢复权限，`3` 表示确认已经安装。
空参数或缓存头读取失败不会覆盖之前的有效安装状态。
`PID` 与 `LastPID/LastStage` 记录最近客户端；多个客户端会依次更新同一组属性，不是每进程状态列表。

在相同硬件、系统和参数下，分别记录 Music 与 TV 的进程 PID、普通音频、受保护内容的声音、
进度、首帧与持续播放。先验证普通音频和已知 MV 的声音未退化，再判断视频是否可用；
尚未记录首帧和持续画面时，不判定视频修复完成。

```sh
ioreg -lw0 -r -c IOPCIDevice | rg 'NRedDRMVideo|NRedVCNFairPlay|NRedVCNUserLastError'
```

## 回归测试

在与生成器输入匹配的 macOS dyld cache 上，先生成补丁表，再运行测试：

```sh
python3 tools/build_video_decode_patch.py
python3 tools/import_legacy_compat.py --check
python3 tools/test_vcn_cp_factory.py
python3 tools/test_legacy_bridges.py --sanitize
python3 tools/test_vcn_user_bridge.py
python3 tools/test_video_cache_dynamic.py --sanitize
```

CP 工厂测试在三个系统的真实函数上分别遍历 65,536 个 family，验证只新增 family 5 的 PSP 分支，
保留无效参数及初始化失败清理；KE 分发器错误透传回归仍使用 26 的真实函数。
桥接测试覆盖 Music／TV／任意名称的真实和缺失权限、默认启用、视频总开关、系统和设备门限、
未选页不写入、逐项写入／权限失败回滚、重复查询状态。
动态缓存测试对 Music／TV／任意名称重复验证重排、跨页及回滚。
这些测试检查兼容策略，不执行 DRM 解密或验证实际音频播放。
