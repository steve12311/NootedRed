# VPF 验证说明

## 自动检查范围

- 清单测试检查生成结果、数据一致性、站点字节、边界和头文件编译。
- 计划与基线测试比较合成缓存中的站点、原始/替换字节、读取次数和摘要。
- Video 等价测试对照既有 Resolver 和当前计划，覆盖 Darwin 24/25、四个视频设备以及两组守卫。
- 事务、账本和 MapGate 测试覆盖安装顺序、失败回滚、隔离、最终 map 销毁、地址复用及并发。
- 离线语义和站点审计只提供分析结果，不扩大运行时能力或准入范围。

测试在合成缓存和用户态夹具中运行，不能替代内核和真实硬件验证。真实参考缓存测试仍依赖
缓存 UUID、映像和基址核验，不应通过放宽校验来运行。

## Display 检查范围

显示清单、页面计划、组依赖和跳板生命周期由独立探针检查；协调器测试覆盖原生单次调用、
同 map 串行、异 map 并行、容量和唤醒；Metal 事务测试覆盖读回、跨页、写入、保护恢复及回滚。
OpenGL 页面和证据测试覆盖精确/动态匹配、变体拒绝与只读采集；内核证据探针使用合成数据。
离线语义审计不生成可安装 Manifest。

```sh
python3 tools/dvpf/generate_display_manifest.py --check
python3 tools/dvpf/freeze_display_baseline.py --check
python3 tools/test_display_baseline.py
python3 tools/test_display_manifest.py
python3 tools/test_display_plan_equivalence.py
python3 tools/test_display_capability_state.py
python3 tools/test_display_trampoline_lifecycle.py
python3 tools/test_display_cross_module_gate.py
python3 tools/test_display_transaction_executor.py
python3 tools/test_vpf_gl_page_adapter.py
python3 tools/test_vpf_gl_dynamic_variants.py
python3 tools/test_vpf_gl_mounted_evidence.py
python3 tools/test_kernel_display_evidence.py
python3 tools/test_vpf_display_semantic_variants.py
python3 tools/vpf/build_gl_evidence.py --fixtures --check
python3 tools/vpf/analyze_display_variants.py --fail-on-rejected
```

真实系统卷检查需要设置 `NRED_GL_SYSTEM_ROOT`；未配置时只跳过依赖该卷的测试。
合成页、用户态探针和只读文件证据不能替代内核 Hook、窗口绘制、Fence、Compute、
显示模式及多进程并发的实机验证。

## 构建与验证边界

既有记录显示 Debug、Research Release 和 Release 构建及静态分析曾通过；Video 等价和生命周期
短回归已运行。最终生命周期 Hook 调整后，完整的长时间共享缓存桥接矩阵没有重新执行。

包含最终 map 销毁 Hook 的构建尚未完成实机启动、视频、编码、休眠唤醒及 WindowServer
验证。较早构建的只读运行记录不能证明当前 Hook、隔离及 map 生命周期行为。
内核调用栈、真实 `copyin` 性能以及锁竞争也不能仅由用户态探针证明。

项目要求对三个配置分别构建和运行静态分析：

```sh
xcodebuild -configuration Debug -arch x86_64 build
xcodebuild -configuration 'Research Release' -arch x86_64 build
xcodebuild -configuration Release -arch x86_64 build
xcodebuild analyze -configuration Debug -arch x86_64
xcodebuild analyze -configuration 'Research Release' -arch x86_64
xcodebuild analyze -configuration Release -arch x86_64
```
