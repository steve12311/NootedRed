# VPF 验证说明

## 自动检查范围

- 清单测试检查生成结果、数据一致性、站点字节、边界和头文件编译。
- 计划与基线测试比较合成缓存中的站点、原始/替换字节、读取次数和摘要。
- Video 等价测试对照既有 Resolver 和当前计划，覆盖 Darwin 24/25、四个视频设备以及两组守卫。
- 事务、账本和 MapGate 测试覆盖安装顺序、失败回滚、隔离、最终 map 销毁、地址复用及并发。
- 离线语义和站点审计只提供分析结果，不扩大运行时能力或准入范围。

测试在合成缓存和用户态夹具中运行，不能替代内核和真实硬件验证。真实参考缓存测试仍依赖
缓存 UUID、映像和基址核验，不应通过放宽校验来运行。

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
