# FairPlay 客户端的 PSP 兼容策略

## 行为与边界

`-NRedVideoFix` 在 macOS 14/15/26 / Darwin 23/24/25、受支持的 VCN 2.2 APU 上，默认
为所有持有 `com.apple.private.fpsd.client=true` 签名权限的客户端采用同一策略。
不使用 Music、TV 或辅助服务的进程名判断是否启用；仅有相同进程名而没有该权限也不会进入此路径。
不再使用 `-NRedDRMVideo` 单独开关；缺少 `-NRedVideoFix` 时不注册桥接。

- 14/15/26 各选择 18 项 AMD 驱动侧解码补丁，掩码分别为
  `0x7FE1FE` / `0x7FE1FE` / `0x7F87FE`。AppleGVA 与编码代码保持原始字节。
  三个系统包含 swizzle 消费者修正；pitch 属于编码路径，仅普通客户端应用。
- 原生 `VACPFactory::createCP` 的 PSP family 范围由 1–4 扩展到 1–5，承接 APU 识别所用的 family 5。
  family 0 继续走 SAMU，其他值继续沿用原生分支；保留对象初始化失败清理及 KE 原始错误码。
- 14/15 的 PSP 工厂分别为 182/183 字节，生产生成器使用对应原生函数摘要和版本专属比较位置，
  只将立即数 `4` 改为 `5`；三系统共用完整函数／引用核验和 COW 回滚事务。
- 固定缓存与动态重定位均检查原生代码；未选项不符合原始字节、混合补丁或不匹配的缓存会拒绝写入。
  写入失败回滚，完成后恢复页面权限。
- 不修改 FP 固件、证书、密钥、可信平台选择字段、认证结果或 AppleGVA 持久化偏好。

该策略不修复固件证书或完整视频保护链；补丁安装成功不表示受保护内容播放成功。

## 诊断与播放回归

`NRedDRMVideoRevision=5` 表示三系统统一权限策略。
14/15 均为 `PatchCount=18`、`Mask=8380926`；
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
python3 tools/test_display_runtime_bridge.py --sanitize
python3 tools/test_video_cache_dynamic.py --sonoma --sanitize
python3 tools/test_video_cache_dynamic.py --sequoia --sanitize
python3 tools/test_video_cache_dynamic.py --sanitize
```

CP 工厂测试在三个系统的真实函数上分别遍历 65,536 个 family，验证只新增 family 5 的 PSP 分支，
保留无效参数及初始化失败清理；KE 分发器错误透传回归仍使用 26 的真实函数。
动态缓存测试对 Music／TV／任意名称重复验证重排、跨页及回滚。
这些测试检查兼容策略，不执行 DRM 解密或验证实际音频播放。
