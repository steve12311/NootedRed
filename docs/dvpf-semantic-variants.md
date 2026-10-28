# 显示补丁：显示有界语义变体报告

> 本报告只做离线字节/引用审计，不生成可安装 Manifest，也不改变运行时准入。
> 生成命令：`python3 tools/vpf/analyze_display_variants.py --json ... --markdown ... --fail-on-rejected`。

## 结论

- 当前生成数据显示 **69 个站点**（36 个 OpenGL 页块、30 个 Metal SRD/Blend/Scratch 站点、
  3 个 SubmitGuard 入口）。
- 69 个站点全部通过“完整原始/替换字节 + 显式引用范围 + 动态字段边界”校验。
- 6 个 Darwin 24 `DisplaySRD` 站点带有全部差异段均不超过 8 字节的立即数字段，列为
  **人工复核候选**；`emittedManifests` 恒为 0。
- 没有站点使用通配 mask、短签名或截断字节；报告对 2 字节 LegacyBlend/PipelineConstructor/
  SubmitGuard 精确载荷单独标记为静态载荷，而不是把它当作可扫描短签名。

## 规则

1. 每个站点必须提供与 `size` 完全一致的原始和替换字节；缺失、截断或长度不符即拒绝。
2. rel32/引用字段由显式 `Reference` 描述；这些字节不参与“语义差异段”统计，候选也不得
   修改引用字段。
3. 立即数候选必须显式列出偏移/长度，且：
   - 所有变化都落在声明的立即数范围内；
   - 引用字段和其余字节与完整替换逐字节一致；
   - 不允许 `mask`、`wildcard`、`signature`、`signatureLength`、`pattern` 等字段。
4. 候选只表示“字节层面可复核”，不证明寄存器活性、CFG 等价或 GPU 执行等价。

## 当前候选

| 站点 | 立即数段数 | 说明 |
| --- | ---: | --- |
| `DisplaySRD_Darwin24_1638#1` | 2 | 两处 1 字节立即数，其余字节与参考替换一致 |
| `DisplaySRD_Darwin24_15D8#1` | 2 | 同上 |
| `DisplaySRD_Darwin24_15DD#1` | 2 | 同上 |
| `DisplaySRD_Darwin24_15E7#1` | 2 | 同上 |
| `DisplaySRD_Darwin24_1636#1` | 2 | 同上 |
| `DisplaySRD_Darwin24_164C#1` | 2 | 同上 |

这些候选尚未经过反汇编/CFG、寄存器活性和实机画面复核，因此不能进入 Manifest，也不能被
描述为“已证明的语义变体”。

## 未纳入的变体

- 通配 mask、短签名、在多个站点间拼接的候选；
- 任何跨页/跨函数的重定位重写；
- 仅凭 UUID 或单个局部字节的兼容性判断。

## 限制

- 报告使用仓库内生成表和夹具，不重新运行真实 dyld/GL 文件解析；真实卷证据需单独核验。
- Metal 函数引用只核对生成站点的类型化字段，不重新执行运行时唯一性扫描。
- 没有实机启动、窗口绘制、HDR/Flip 或 GPU 性能证据；这些不能由本报告推出。
