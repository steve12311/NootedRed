# Vega APU Metal 渲染与 Scratch 修复

Darwin 23/24/25 使用统一显示入口 `-NRedDisplayFix`，启用 SRD、LegacyBlend、ComputeScratch
和 SubmitGuard，保留原生同步策略。完整版本与生成说明见 [多系统适配](macos-multi-os-compat.md)。

设备范围为 `15D8 / 15DD / 15E7 / 1636 / 1638 / 164C`。生成器复用 NRed 的 PCI 分类，
只修改匹配设备的设置；同一进程内其他 GPU 保留原生行为。

- `X5000` 的计算提交虚表修复跳过 Itanium ABI 的两个头部槽位，并核对原提交函数地址。
- SRD 使用 shared 表分配，保留原生同步策略与配置读取。
- LegacyBlend 调整 RB+ 能力分支，保留 `dualSource || !allowRBPlus` 与状态写入。
- ComputeScratch 对匹配的 APU 跳过虚拟 SE 乘法；Shader Array、CU 与资源分配尾部保持原生行为。
- SubmitGuard 核验完整提交方法、相对引用和已安装跳板，按事务安装并恢复 RX 权限。

所有 UUID 均须经过完整函数与实际引用核验。`DynamicMatched` 表示解析通过，
`DynamicApplied` 表示写入并恢复权限；它们不证明 GPU 渲染结果正确。
损坏字节、错误绑定、混合状态和不兼容布局均拒绝安装。

## 验证

```sh
python3 tools/test_display_runtime_bridge.py --sanitize
python3 tools/test_compute_cache_dynamic.py --sonoma --sanitize
python3 tools/test_compute_cache_dynamic.py --sequoia --sanitize
python3 tools/test_compute_cache_dynamic.py --sanitize
python3 tools/test_legacy_blend_multi_os.py
python3 tools/test_compute_vtable.py
```

原生初始化、Scratch 和实际缓存检查需要对应版本缓存；命令见多系统适配文档。
离线验证不替代硬件上的显示、持续运行及睡眠唤醒验收。
