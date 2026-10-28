# DVPF 显示基线

> 冻结基线：`31fcc872807ae06f59d99016dd5ef80d8996e8ec`；参考代码基线：`6fb1aa6df07243c44541cbd3ccf5371804c4a804`。
> 本文件由 `python3 tools/dvpf/freeze_display_baseline.py --write` 生成，不手工编辑。

本基线记录显示补丁字节、解析器、设备白名单、启动参数和源码哈希。
离线证据和用户态测试不改变运行时准入。

## 1. 不变量

- 唯一显示总开关是 `-NRedDisplayFix`；`-NRedImmediateSync`、`-NRedSrdShared`、`-NRedLegacyBlend`、
  `-NRedComputeScratch` 保持停用。
- OpenGL 只修改当前已验证的 4 KiB 页内完整块；Metal 只使用当前 task 的 `vm_map` 和私有 COW 页；
  内核 GFX/DCN 不套用用户态事务。
- SubmitGuard 的入口跳板和守卫页生命周期独立于普通渲染组；入口无法确认恢复时保留目标页。
- 本基线中的 `Applied` 标记不等同于 GPU 执行或 HDR 显示已验收。

## 2. 源码与夹具快照

| 文件 | SHA-256 |
| --- | --- |
| `NootedRed/AMDGFX9DCNDisplay.cpp` | `ba9837256a05d4c2c12bdd986c6e061fb143404a59806fccec274f97d81db149` |
| `NootedRed/KernelDisplayEvidence.cpp` | `cfb1861b6ecf5d05363910c7e5157b0c977e29c784c1d364fb35675474589cea` |
| `NootedRed/KernelDisplayEvidence.hpp` | `ccc2b6c1e0f006073bfe58909f088dd74a9c1e21e689be0561f389ac19591895` |
| `NootedRed/NRed.cpp` | `c7f8814f66bba6b9e74ca291fd50d2b1b24b7246ee6a2e4edddd405e368c0224` |
| `NootedRed/UserCacheResolver.hpp` | `bf96aaffa72ede4462ca95a3b1dca57c47e79d25f8598ce84eb24ab5112643d0` |
| `NootedRed/UserLegacyData.hpp` | `34d4ee4238ad37c300cc86773c2fae7d10005a283e98bfcf01b9dbceecab329c` |
| `NootedRed/UserOpenGL.cpp` | `6c51c630aff84526fec2232609f3173c0575177e39acbdd261bd3df8b905aff5` |
| `NootedRed/UserOpenGLData.hpp` | `052dbfa0e4c1576f2213dac592a795eff3038ec535289eeb1b0425cf10a4a67b` |
| `NootedRed/UserOpenGLResolver.hpp` | `42acd20b12135ec57e4a1d30b9be6b21802d0785f709b7a5c9b2c55895ee3f3d` |
| `NootedRed/UserPatchCoordinator.cpp` | `bceade48bdfaa078d325c2ec87008c3b5fe710d0e2e2363c07381890e72476af` |
| `NootedRed/UserPatchCoordinator.hpp` | `c7296b2be88c511773a1117e5347b12b350fd0e25ce4073349c9d8d3902a7c47` |
| `NootedRed/UserPatchMapGate.hpp` | `6d0b86ad16a7453f85f4e03a087eeb6e7ee6c557deaa51648a951e492f8144c0` |
| `NootedRed/UserPatchMemory.hpp` | `aed903b82c278a2a5eeac726f9eb39cda2877f8d74d1574cabf291e42571335b` |
| `NootedRed/UserSequoiaSurfaceData.hpp` | `ae57ebe6b10ec20b00d25d2e6687b1c7e1339fdeda9be1556aa8c7e5728371dd` |
| `NootedRed/UserSharedCache.hpp` | `9b9dc85b3b6c829847b57f1a41b641182852c4d4536f6aa5ef3918ebb2a6966d` |
| `NootedRed/UserSubmitGuardData.hpp` | `f23b65b31e472ffff19636960fe547dc8ad6dc32c1986ea48ed7f54030fc6077` |
| `NootedRed/UserSurfaceConstructorData.hpp` | `8a8b5a05c9b06c11f29a565b3c9096c5bbc9fc940e00e3c0c9af9be2d175bfd2` |
| `NootedRed/UserSurfaceSync.cpp` | `04e715c14b2ac22d985cfad1f6d04a440643cd196f552a6b4d7280443f1936ac` |
| `NootedRed/UserSurfaceSyncData.hpp` | `a7a39e004aa0f0e8f787de913abeaab2c08ff60023ea7f61e34391b80cf179a3` |
| `NootedRed/UserSurfaceSyncResolver.hpp` | `845ec4417ec13455f6b032df6755f0ed99399575461218106600c3cfd5e0594e` |
| `NootedRed/UserVideoDecode.cpp` | `567213057f72a61afead4e21eb3ca26b32ccb4e0c0ffb9fa9f735864299f6e1d` |
| `NootedRed/VPF/DisplayManifestData.hpp` | `7865d9fdf3eeb753cdaa029de18b125f4512c3681370cf8ebd8b1b24ab7c2761` |
| `NootedRed/VPF/DisplayPlan.hpp` | `58af326be437dcf0fcf0b5eebc2b815a07ad440f2dcd6d8ac1c82d5e726a7006` |
| `NootedRed/VPF/DisplayPlanAdapters.hpp` | `c811f717d97356c911a9bdb8c8921ac0a57ea4d27eb9e9a335d12b9bbe60288a` |
| `NootedRed/VPF/ExecutableTrampolineResource.hpp` | `b2a6737b7a4833802ac7ab67fefed436390a9ed61f03773a6b854ce93e5ca0d5` |
| `NootedRed/VPF/GLManifestData.hpp` | `ade5ab25a92be138695a3933891be4767388c7ebd9ab161ed600cc60098a17c2` |
| `NootedRed/VPF/MetalTransactionExecutor.hpp` | `db17db74348da5cf6603d2807fc11c7f9d88f307e4cd344b954cbc190e8936ca` |
| `NootedRed/VPF/ValidatedPageBackend.hpp` | `7a8fbf89f88a4952a4fa6f710986d356320660d8054e0593567648de72e16bf3` |
| `NootedRed/VPF/VerifiedPatchPlan.hpp` | `b45e79d2102f73843ab66f1b9aa37b95558a1d9a293b8605a04e49913c0b2954` |
| `NootedRed/X5000.cpp` | `13967daf8ea58447fc1c3482e05ac91f81430861585bc4cc73f1f946eb07ebb5` |
| `NootedRed/X6000FB.cpp` | `aefd17aaf24fcfdad36b93e4401707fbbed3414544b5478c99075354b33e41e3` |
| `tools/build_cache_section_targets.py` | `e227011183e39724a714d4bec0066da1ec098f5d90a8f1271f757b5c8de46b7e` |
| `tools/build_opengl_patch.py` | `0427baa3682aafab4f7cd1601ba9965347cbd0e3b09c977cce54af35caf0f306` |
| `tools/build_sequoia_surface.py` | `e1b9fba0a88ff00413f4971de09eb887602973ffe47a2acabe1827b21d4b6721` |
| `tools/build_submit_guard.py` | `036c9d42416e68cc0b90e73e825f354c56367c538f5e4810b8184f934892807c` |
| `tools/build_surface_sync_patch.py` | `29fd166cbb88bd050abaaa755f68decd9e79f0cfd97175400b8c20690ebc84fb` |
| `tools/dvpf/gl_page_adapter_probe.cpp` | `0130b24c24a97eb65b74d4210137abeffc5e1cabc8e863a4a19a51e37a0d5e8c` |
| `tools/fixtures/legacy-compat.json` | `e0b46680b6206a13e532bb6181c715acd89244da01f02a8ec259bdf402c9c19f` |
| `tools/fixtures/opengl-compat.json` | `ed16a90f351d717a454f1c82ef60f42140680e0e8a1054163cce07040c6fad62` |
| `tools/fixtures/sequoia-compat.json` | `2e6ab11f6a7c9c10394978c980168ebb1b01c79113c788b712468bb4b5a59bfd` |
| `tools/import_legacy_compat.py` | `ecea0e4e7f3ab37471fb471fbaa872f618111f5753c7b07684ee28aa541b1d99` |
| `tools/vpf/analyze_display_variants.py` | `7b2d181b25f423758da3d93b852de38c29b93b599a91cdd90ef470941484e0d9` |
| `tools/vpf/build_gl_evidence.py` | `63d8f97b22f422ca94ce6e2168dd40ebcd391d27c0cb1a587ca0ee6ba2f96fb1` |
| `tools/vpf/display_semantics.py` | `5ea732fdaefa26bd432213bad5b71eea72e9733493ab31fa31245774efcfc91e` |
| `tools/vpf/gl_dynamic_variants_probe.cpp` | `264912a601a916dab5f91ce08a91aab92b75b762adb454440f2227b33c945ed0` |
| `tools/vpf/gl_evidence.py` | `3e697556732d9784f5723470939772bc8202af91cf05e0fc56a89f9b28b7d5ce` |

## 3. 启动参数与诊断属性

- 启用：`-NRedDisplayFix`
- 停用：`-NRedImmediateSync`, `-NRedSrdShared`, `-NRedLegacyBlend`, `-NRedComputeScratch`

显示路径当前发布的属性（仅诊断，不代表安装成功）：

- `NRedCacheSectionRevision`
- `NRedComputeScratchApplied`
- `NRedComputeScratchEnabled`
- `NRedComputeScratchVersion`
- `NRedComputeScratchWeatherApplied`
- `NRedComputeScratchWeatherPID`
- `NRedDisplayFixEnabled`
- `NRedDisplayFixVersion`
- `NRedImmediateSyncApplied`
- `NRedImmediateSyncDisabled`
- `NRedImmediateSyncDynamicApplied`
- `NRedImmediateSyncDynamicRejected`
- `NRedImmediateSyncLastError`
- `NRedImmediateSyncRegionQueryVersion`
- `NRedImmediateSyncRejected`
- `NRedImmediateSyncRequested`
- `NRedImmediateSyncRevision`
- `NRedImmediateSyncVersion`
- `NRedImmediateSyncWindowServerApplied`
- `NRedImmediateSyncWindowServerPID`
- `NRedKernelComputeVTable`
- `NRedKernelDisplayEvidenceRevision`
- `NRedKernelFlipFormat0`
- `NRedKernelFlipFormat1`
- `NRedKernelFlipFormat2`
- `NRedKernelFlipFormat3`
- `NRedKernelFlipPitch0`
- `NRedKernelFlipPitch1`
- `NRedKernelFlipPitch2`
- `NRedKernelFlipPitch3`
- `NRedKernelFlipRotation0`
- `NRedKernelFlipRotation1`
- `NRedKernelFlipRotation2`
- `NRedKernelFlipRotation3`
- `NRedKernelFlipWaitCount`
- `NRedKernelFlipWaitMax`
- `NRedKernelFlipWaitTotal`
- `NRedKernelFramebufferDarwin`
- `NRedKernelFramebufferFlags`
- `NRedKernelGFX9Attributes`
- `NRedKernelGFX9DeviceID`
- `NRedKernelGFX9FbOffsetHigh`
- `NRedKernelGFX9FbOffsetLow`
- `NRedKernelGFX9Revisions`
- `NRedKernelKextError`
- `NRedKernelKextValid`
- `NRedKernelScanoutChanges`
- `NRedKernelScanoutFormat0`
- `NRedKernelScanoutFormat1`
- `NRedKernelScanoutFormat2`
- `NRedKernelScanoutFormat3`
- `NRedKernelScanoutSwizzle0`
- `NRedKernelScanoutSwizzle1`
- `NRedKernelScanoutSwizzle2`
- `NRedKernelScanoutSwizzle3`
- `NRedKernelX5000KextSize`
- `NRedKernelX5000KextUUID0`
- `NRedKernelX5000KextUUID1`
- `NRedKernelX5000KextUUID2`
- `NRedKernelX5000KextUUID3`
- `NRedKernelX5000SourceVersionHigh`
- `NRedKernelX5000SourceVersionLow`
- `NRedKernelX6000FBKextSize`
- `NRedKernelX6000FBKextUUID0`
- `NRedKernelX6000FBKextUUID1`
- `NRedKernelX6000FBKextUUID2`
- `NRedKernelX6000FBKextUUID3`
- `NRedKernelX6000FBSourceVersionHigh`
- `NRedKernelX6000FBSourceVersionLow`
- `NRedLegacyBlendApplied`
- `NRedLegacyBlendEnabled`
- `NRedLegacyBlendRejected`
- `NRedLegacyBlendVersion`
- `NRedLegacyBlendWindowServerApplied`
- `NRedLegacyBlendWindowServerPID`
- `NRedOpenGLBackends`
- `NRedOpenGLDualQuadApplied`
- `NRedOpenGLDynamicApplied`
- `NRedOpenGLEnabled`
- `NRedOpenGLPageLocal`
- `NRedOpenGLRejected`
- `NRedOpenGLRestoreFailed`
- `NRedOpenGLRevision`
- `NRedOpenGLSEApplied`
- `NRedOpenGLVPFEvidenceMask`
- `NRedOpenGLVPFManifestMask`
- `NRedOpenGLVPFPlanVerified`
- `NRedOpenGLVPFRevision`
- `NRedOpenGLWriteRejected`
- `NRedPatchCoordinatorDisplay`
- `NRedPatchCoordinatorEnabled`
- `NRedPatchCoordinatorPeak`
- `NRedPatchCoordinatorRejected`
- `NRedPatchCoordinatorRevision`
- `NRedPatchCoordinatorSkipped`
- `NRedPatchCoordinatorVideo`
- `NRedSharedRegionStackRevision`
- `NRedSrdSharedApplied`
- `NRedSrdSharedEnabled`
- `NRedSrdSharedVersion`
- `NRedSrdSharedWindowServerApplied`
- `NRedSrdSharedWindowServerPID`
- `NRedSubmitGuardApplied`
- `NRedSubmitGuardDisabled`
- `NRedSubmitGuardDynamicRejected`
- `NRedSubmitGuardEnabled`
- `NRedSubmitGuardRejected`
- `NRedSubmitGuardRevision`
- `NRedSubmitGuardRollbackFailed`
- `NRedSubmitGuardVersion`
- `NRedSubmitGuardWindowServerApplied`
- `NRedSubmitGuardWindowServerPID`

## 4. 设备白名单

来自 `NRed.cpp` 的 APU 分类：

`0x1638`, `0x15D8`, `0x15DD`, `0x15E7`, `0x1636`, `0x164C`

## 5. 显示功能清单

| 功能 | Domain | Group | Darwin | Device | Evidence | 站点 | 最大字节 | 来源 |
| --- | --- | --- | --- | ---: | --- | ---: | ---: | --- |
| `OpenGLHwInfo` | OpenGLValidatedPage | GLHwInfo | 23 | 0x15D8 | ExactKnownPage | 1 | 256 | `UserOpenGLData.hpp` |
| `OpenGLHwInfo` | OpenGLValidatedPage | GLHwInfo | 23 | 0x15DD | ExactKnownPage | 1 | 256 | `UserOpenGLData.hpp` |
| `OpenGLHwInfo` | OpenGLValidatedPage | GLHwInfo | 23 | 0x15E7 | ExactKnownPage | 1 | 256 | `UserOpenGLData.hpp` |
| `OpenGLHwInfo` | OpenGLValidatedPage | GLHwInfo | 23 | 0x1636 | ExactKnownPage | 1 | 256 | `UserOpenGLData.hpp` |
| `OpenGLHwInfo` | OpenGLValidatedPage | GLHwInfo | 23 | 0x1638 | ExactKnownPage | 1 | 256 | `UserOpenGLData.hpp` |
| `OpenGLHwInfo` | OpenGLValidatedPage | GLHwInfo | 23 | 0x164C | ExactKnownPage | 1 | 256 | `UserOpenGLData.hpp` |
| `OpenGLHwInfo` | OpenGLValidatedPage | GLHwInfo | 24 | 0x15D8 | ExactKnownPage | 1 | 256 | `UserOpenGLData.hpp` |
| `OpenGLHwInfo` | OpenGLValidatedPage | GLHwInfo | 24 | 0x15DD | ExactKnownPage | 1 | 256 | `UserOpenGLData.hpp` |
| `OpenGLHwInfo` | OpenGLValidatedPage | GLHwInfo | 24 | 0x15E7 | ExactKnownPage | 1 | 256 | `UserOpenGLData.hpp` |
| `OpenGLHwInfo` | OpenGLValidatedPage | GLHwInfo | 24 | 0x1636 | ExactKnownPage | 1 | 256 | `UserOpenGLData.hpp` |
| `OpenGLHwInfo` | OpenGLValidatedPage | GLHwInfo | 24 | 0x1638 | ExactKnownPage | 1 | 256 | `UserOpenGLData.hpp` |
| `OpenGLHwInfo` | OpenGLValidatedPage | GLHwInfo | 24 | 0x164C | ExactKnownPage | 1 | 256 | `UserOpenGLData.hpp` |
| `OpenGLHwInfo` | OpenGLValidatedPage | GLHwInfo | 25 | 0x15D8 | ExactKnownPage | 1 | 256 | `UserOpenGLData.hpp` |
| `OpenGLHwInfo` | OpenGLValidatedPage | GLHwInfo | 25 | 0x15DD | ExactKnownPage | 1 | 256 | `UserOpenGLData.hpp` |
| `OpenGLHwInfo` | OpenGLValidatedPage | GLHwInfo | 25 | 0x15E7 | ExactKnownPage | 1 | 256 | `UserOpenGLData.hpp` |
| `OpenGLHwInfo` | OpenGLValidatedPage | GLHwInfo | 25 | 0x1636 | ExactKnownPage | 1 | 256 | `UserOpenGLData.hpp` |
| `OpenGLHwInfo` | OpenGLValidatedPage | GLHwInfo | 25 | 0x1638 | ExactKnownPage | 1 | 256 | `UserOpenGLData.hpp` |
| `OpenGLHwInfo` | OpenGLValidatedPage | GLHwInfo | 25 | 0x164C | ExactKnownPage | 1 | 256 | `UserOpenGLData.hpp` |
| `OpenGLDualQuad` | OpenGLValidatedPage | GLDualQuad | 23 | 0x15D8 | ExactKnownPage | 1 | 142 | `UserOpenGLData.hpp` |
| `OpenGLDualQuad` | OpenGLValidatedPage | GLDualQuad | 23 | 0x15DD | ExactKnownPage | 1 | 142 | `UserOpenGLData.hpp` |
| `OpenGLDualQuad` | OpenGLValidatedPage | GLDualQuad | 23 | 0x15E7 | ExactKnownPage | 1 | 142 | `UserOpenGLData.hpp` |
| `OpenGLDualQuad` | OpenGLValidatedPage | GLDualQuad | 23 | 0x1636 | ExactKnownPage | 1 | 142 | `UserOpenGLData.hpp` |
| `OpenGLDualQuad` | OpenGLValidatedPage | GLDualQuad | 23 | 0x1638 | ExactKnownPage | 1 | 142 | `UserOpenGLData.hpp` |
| `OpenGLDualQuad` | OpenGLValidatedPage | GLDualQuad | 23 | 0x164C | ExactKnownPage | 1 | 142 | `UserOpenGLData.hpp` |
| `OpenGLDualQuad` | OpenGLValidatedPage | GLDualQuad | 24 | 0x15D8 | ExactKnownPage | 1 | 142 | `UserOpenGLData.hpp` |
| `OpenGLDualQuad` | OpenGLValidatedPage | GLDualQuad | 24 | 0x15DD | ExactKnownPage | 1 | 142 | `UserOpenGLData.hpp` |
| `OpenGLDualQuad` | OpenGLValidatedPage | GLDualQuad | 24 | 0x15E7 | ExactKnownPage | 1 | 142 | `UserOpenGLData.hpp` |
| `OpenGLDualQuad` | OpenGLValidatedPage | GLDualQuad | 24 | 0x1636 | ExactKnownPage | 1 | 142 | `UserOpenGLData.hpp` |
| `OpenGLDualQuad` | OpenGLValidatedPage | GLDualQuad | 24 | 0x1638 | ExactKnownPage | 1 | 142 | `UserOpenGLData.hpp` |
| `OpenGLDualQuad` | OpenGLValidatedPage | GLDualQuad | 24 | 0x164C | ExactKnownPage | 1 | 142 | `UserOpenGLData.hpp` |
| `OpenGLDualQuad` | OpenGLValidatedPage | GLDualQuad | 25 | 0x15D8 | ExactKnownPage | 1 | 142 | `UserOpenGLData.hpp` |
| `OpenGLDualQuad` | OpenGLValidatedPage | GLDualQuad | 25 | 0x15DD | ExactKnownPage | 1 | 142 | `UserOpenGLData.hpp` |
| `OpenGLDualQuad` | OpenGLValidatedPage | GLDualQuad | 25 | 0x15E7 | ExactKnownPage | 1 | 142 | `UserOpenGLData.hpp` |
| `OpenGLDualQuad` | OpenGLValidatedPage | GLDualQuad | 25 | 0x1636 | ExactKnownPage | 1 | 142 | `UserOpenGLData.hpp` |
| `OpenGLDualQuad` | OpenGLValidatedPage | GLDualQuad | 25 | 0x1638 | ExactKnownPage | 1 | 142 | `UserOpenGLData.hpp` |
| `OpenGLDualQuad` | OpenGLValidatedPage | GLDualQuad | 25 | 0x164C | ExactKnownPage | 1 | 142 | `UserOpenGLData.hpp` |
| `MetalSrdShared` | MetalSharedCache | MetalRenderCore | 24 | 0x15D8 | FullFunctionWithRefs | 4 | 456 | `UserSequoiaSurfaceData.hpp` |
| `MetalSrdShared` | MetalSharedCache | MetalRenderCore | 24 | 0x15DD | FullFunctionWithRefs | 4 | 456 | `UserSequoiaSurfaceData.hpp` |
| `MetalSrdShared` | MetalSharedCache | MetalRenderCore | 24 | 0x15E7 | FullFunctionWithRefs | 4 | 456 | `UserSequoiaSurfaceData.hpp` |
| `MetalSrdShared` | MetalSharedCache | MetalRenderCore | 24 | 0x1636 | FullFunctionWithRefs | 4 | 456 | `UserSequoiaSurfaceData.hpp` |
| `MetalSrdShared` | MetalSharedCache | MetalRenderCore | 24 | 0x1638 | FullFunctionWithRefs | 4 | 456 | `UserSequoiaSurfaceData.hpp` |
| `MetalSrdShared` | MetalSharedCache | MetalRenderCore | 24 | 0x164C | FullFunctionWithRefs | 4 | 456 | `UserSequoiaSurfaceData.hpp` |
| `MetalSrdShared` | MetalSharedCache | MetalRenderCore | 25 | 0x0000 | ExactStaticPayload | 2 | 215 | `UserSurfaceSyncData.hpp` |
| `MetalLegacyBlend` | MetalSharedCache | MetalRenderCore | 23 | 0x0000 | ExactStaticPayload | 1 | 2 | `UserLegacyData.hpp` |
| `MetalLegacyBlend` | MetalSharedCache | MetalRenderCore | 25 | 0x0000 | ExactStaticPayload | 3 | 215 | `UserSurfaceSyncData.hpp` |
| `MetalComputeScratch` | MetalSharedCache | MetalScratch | 24/25 | 0x0000 | FullFunctionWithRefs | 0 | 512 | `UserSurfaceSyncData.hpp` |
| `MetalPipelineConstructor` | MetalSharedCache | MetalRenderCore | 24/25 | 0x0000 | FullFunctionWithRefs | 1 | 2 | `UserSurfaceConstructorData.hpp` |
| `MetalSubmit` | MetalSharedCache | MetalSubmitSafety | 24/25 | 0x0000 | FullFunctionWithRefs | 0 | 0 | `UserSurfaceConstructorData.hpp` |
| `MetalSubmitGuard` | MetalSharedCache | MetalSubmitSafety | 23 | 0x0000 | RuntimeTrampoline | 1 | 15 | `UserSubmitGuardData.hpp` |
| `MetalSubmitGuard` | MetalSharedCache | MetalSubmitSafety | 24 | 0x0000 | RuntimeTrampoline | 1 | 15 | `UserSubmitGuardData.hpp` |
| `MetalSubmitGuard` | MetalSharedCache | MetalSubmitSafety | 25 | 0x0000 | RuntimeTrampoline | 1 | 15 | `UserSubmitGuardData.hpp` |
| `KernelGFX9Capabilities` | KernelGraphics | KernelGFX9 | 23/24/25 | 0x0000 | KernelSymbolAndABI | 0 | 0 | `X5000.cpp` |
| `KernelDCNScanout` | KernelFramebuffer | KernelScanout | 23/24/25 | 0x0000 | KernelSymbolAndABI | 0 | 0 | `X6000FB.cpp` |
| `KernelFramebuffer` | KernelFramebuffer | KernelScanout | 23/24/25 | 0x0000 | KernelSymbolAndABI | 0 | 0 | `AMDGFX9DCNDisplay.cpp` |
| `HDRResearch` | GPUObservation | HDRResearch | 23/24/25 | 0x0000 | DescriptiveOnly | 0 | 0 | `docs/dvpf-architecture.md` |

## 6. 补丁站点与字节摘要

站点顺序与生成头一致；完整原始/替换字节见基线 JSON。OpenGL 站点另记录参考页范围与
相对引用目标；Metal 站点的完整函数/段节证据由 `VPF::ManifestData` 和
`tools/vpf/fixtures/baseline.json` 保存。

| Key | # | 文件/函数偏移 | 大小 | 原始 SHA-256 | 替换 SHA-256 | 函数 | 指令偏移 | 页范围 |
| --- | ---: | ---: | ---: | --- | --- | ---: | ---: | --- |
| `OpenGLHwInfo` | 0 | `0xEB04` | 256 | `4d8d98f4507a9634` | `d05b28e6d92b860d` | 0 | `0x128` | 0xE000+0xB04 |
| `OpenGLHwInfo` | 0 | `0xEB04` | 256 | `4d8d98f4507a9634` | `1eec4188366d97f4` | 0 | `0x128` | 0xE000+0xB04 |
| `OpenGLHwInfo` | 0 | `0xEB04` | 256 | `4d8d98f4507a9634` | `a1b88e204c2eb43b` | 0 | `0x128` | 0xE000+0xB04 |
| `OpenGLHwInfo` | 0 | `0xEB04` | 256 | `4d8d98f4507a9634` | `20666f30a92febd8` | 0 | `0x128` | 0xE000+0xB04 |
| `OpenGLHwInfo` | 0 | `0xEB04` | 256 | `4d8d98f4507a9634` | `00b463c731e5bc56` | 0 | `0x128` | 0xE000+0xB04 |
| `OpenGLHwInfo` | 0 | `0xEB04` | 256 | `4d8d98f4507a9634` | `df355ebe826b50fd` | 0 | `0x128` | 0xE000+0xB04 |
| `OpenGLHwInfo` | 0 | `0xE068` | 256 | `b46e6156c1ef58e8` | `60ce56613246de1e` | 0 | `0x128` | 0xE000+0x68 |
| `OpenGLHwInfo` | 0 | `0xE068` | 256 | `b46e6156c1ef58e8` | `a7f33350378073b8` | 0 | `0x128` | 0xE000+0x68 |
| `OpenGLHwInfo` | 0 | `0xE068` | 256 | `b46e6156c1ef58e8` | `8c06b61ddd7f8a40` | 0 | `0x128` | 0xE000+0x68 |
| `OpenGLHwInfo` | 0 | `0xE068` | 256 | `b46e6156c1ef58e8` | `3e4d5bec518839fa` | 0 | `0x128` | 0xE000+0x68 |
| `OpenGLHwInfo` | 0 | `0xE068` | 256 | `b46e6156c1ef58e8` | `a1d9e94f23839d35` | 0 | `0x128` | 0xE000+0x68 |
| `OpenGLHwInfo` | 0 | `0xE068` | 256 | `b46e6156c1ef58e8` | `c24d2ff5cd080b37` | 0 | `0x128` | 0xE000+0x68 |
| `OpenGLHwInfo` | 0 | `0xE0A8` | 256 | `0f6fd3022241d37f` | `f7ab9b259232c822` | 0 | `0x128` | 0xE000+0xA8 |
| `OpenGLHwInfo` | 0 | `0xE0A8` | 256 | `0f6fd3022241d37f` | `52923c4a23f9cb2e` | 0 | `0x128` | 0xE000+0xA8 |
| `OpenGLHwInfo` | 0 | `0xE0A8` | 256 | `0f6fd3022241d37f` | `1c8886986b1fb7d0` | 0 | `0x128` | 0xE000+0xA8 |
| `OpenGLHwInfo` | 0 | `0xE0A8` | 256 | `0f6fd3022241d37f` | `8141e77c3dcfa12c` | 0 | `0x128` | 0xE000+0xA8 |
| `OpenGLHwInfo` | 0 | `0xE0A8` | 256 | `0f6fd3022241d37f` | `bc5ff51971ba6973` | 0 | `0x128` | 0xE000+0xA8 |
| `OpenGLHwInfo` | 0 | `0xE0A8` | 256 | `0f6fd3022241d37f` | `ea917b166f8372a8` | 0 | `0x128` | 0xE000+0xA8 |
| `OpenGLDualQuad` | 0 | `0x8B28` | 142 | `8af15f73489e402a` | `033b5a61532829ae` | 1 | `0x0` | 0x8000+0xB28 |
| `OpenGLDualQuad` | 0 | `0x8B28` | 142 | `8af15f73489e402a` | `10cb78d554fbdc5f` | 1 | `0x0` | 0x8000+0xB28 |
| `OpenGLDualQuad` | 0 | `0x8B28` | 142 | `8af15f73489e402a` | `51cfce8d20f27788` | 1 | `0x0` | 0x8000+0xB28 |
| `OpenGLDualQuad` | 0 | `0x8B28` | 142 | `8af15f73489e402a` | `047aae0554e46619` | 1 | `0x0` | 0x8000+0xB28 |
| `OpenGLDualQuad` | 0 | `0x8B28` | 142 | `8af15f73489e402a` | `b7e0fa7513d81e4d` | 1 | `0x0` | 0x8000+0xB28 |
| `OpenGLDualQuad` | 0 | `0x8B28` | 142 | `8af15f73489e402a` | `0f4f8c51fed07a77` | 1 | `0x0` | 0x8000+0xB28 |
| `OpenGLDualQuad` | 0 | `0x8134` | 142 | `b5e60f5a10c7836c` | `09cbf0c1737ce387` | 1 | `0x0` | 0x8000+0x134 |
| `OpenGLDualQuad` | 0 | `0x8134` | 142 | `b5e60f5a10c7836c` | `c8d216fcaf68a8ca` | 1 | `0x0` | 0x8000+0x134 |
| `OpenGLDualQuad` | 0 | `0x8134` | 142 | `b5e60f5a10c7836c` | `6c931d0e20452045` | 1 | `0x0` | 0x8000+0x134 |
| `OpenGLDualQuad` | 0 | `0x8134` | 142 | `b5e60f5a10c7836c` | `c199050910b2588c` | 1 | `0x0` | 0x8000+0x134 |
| `OpenGLDualQuad` | 0 | `0x8134` | 142 | `b5e60f5a10c7836c` | `59d5b759f448efbb` | 1 | `0x0` | 0x8000+0x134 |
| `OpenGLDualQuad` | 0 | `0x8134` | 142 | `b5e60f5a10c7836c` | `60a3b037a62dd611` | 1 | `0x0` | 0x8000+0x134 |
| `OpenGLDualQuad` | 0 | `0x819E` | 142 | `2e6f3173c900c04b` | `52f20caee96d5850` | 1 | `0x0` | 0x8000+0x19E |
| `OpenGLDualQuad` | 0 | `0x819E` | 142 | `2e6f3173c900c04b` | `164b6c50c1a3b05a` | 1 | `0x0` | 0x8000+0x19E |
| `OpenGLDualQuad` | 0 | `0x819E` | 142 | `2e6f3173c900c04b` | `12e7a785f74c98b5` | 1 | `0x0` | 0x8000+0x19E |
| `OpenGLDualQuad` | 0 | `0x819E` | 142 | `2e6f3173c900c04b` | `acfe4873b55b420d` | 1 | `0x0` | 0x8000+0x19E |
| `OpenGLDualQuad` | 0 | `0x819E` | 142 | `2e6f3173c900c04b` | `d084f25eec0a5b3b` | 1 | `0x0` | 0x8000+0x19E |
| `OpenGLDualQuad` | 0 | `0x819E` | 142 | `2e6f3173c900c04b` | `484f23acc9214d7c` | 1 | `0x0` | 0x8000+0x19E |
| `MetalSrdShared` | 0 | `0x30886C85C` | 215 | `54ebf2cfa0983975` | `a3bf1a6b2e89dbc3` | 0 | `0x0` |  |
| `MetalSrdShared` | 1 | `0x30886C933` | 10 | `580a055bd41ed3f1` | `5497793b124348f1` | 1 | `0x0` |  |
| `MetalSrdShared` | 2 | `0x3087B3164` | 2 | `525cc8f73fc005eb` | `dfb779f77fa96bfb` | 2 | `0x11C4` |  |
| `MetalSrdShared` | 3 | `0x3087F0771` | 456 | `71cf0143fe4fe704` | `7bb9bc7e3f6a4377` | 3 | `0x0` |  |
| `MetalSrdShared` | 0 | `0x30886C85C` | 215 | `54ebf2cfa0983975` | `d16d22746fe687ce` | 0 | `0x0` |  |
| `MetalSrdShared` | 1 | `0x30886C933` | 10 | `580a055bd41ed3f1` | `5497793b124348f1` | 1 | `0x0` |  |
| `MetalSrdShared` | 2 | `0x3087B3164` | 2 | `525cc8f73fc005eb` | `dfb779f77fa96bfb` | 2 | `0x11C4` |  |
| `MetalSrdShared` | 3 | `0x3087F0771` | 456 | `71cf0143fe4fe704` | `a010c210cd8bfd36` | 3 | `0x0` |  |
| `MetalSrdShared` | 0 | `0x30886C85C` | 215 | `54ebf2cfa0983975` | `9b47e23146e92dd3` | 0 | `0x0` |  |
| `MetalSrdShared` | 1 | `0x30886C933` | 10 | `580a055bd41ed3f1` | `5497793b124348f1` | 1 | `0x0` |  |
| `MetalSrdShared` | 2 | `0x3087B3164` | 2 | `525cc8f73fc005eb` | `dfb779f77fa96bfb` | 2 | `0x11C4` |  |
| `MetalSrdShared` | 3 | `0x3087F0771` | 456 | `71cf0143fe4fe704` | `9e8c5d94d7f0927f` | 3 | `0x0` |  |
| `MetalSrdShared` | 0 | `0x30886C85C` | 215 | `54ebf2cfa0983975` | `8d4e9cdadbe4f2f5` | 0 | `0x0` |  |
| `MetalSrdShared` | 1 | `0x30886C933` | 10 | `580a055bd41ed3f1` | `5497793b124348f1` | 1 | `0x0` |  |
| `MetalSrdShared` | 2 | `0x3087B3164` | 2 | `525cc8f73fc005eb` | `dfb779f77fa96bfb` | 2 | `0x11C4` |  |
| `MetalSrdShared` | 3 | `0x3087F0771` | 456 | `71cf0143fe4fe704` | `d353654635299df3` | 3 | `0x0` |  |
| `MetalSrdShared` | 0 | `0x30886C85C` | 215 | `54ebf2cfa0983975` | `be82213096f5b1da` | 0 | `0x0` |  |
| `MetalSrdShared` | 1 | `0x30886C933` | 10 | `580a055bd41ed3f1` | `5497793b124348f1` | 1 | `0x0` |  |
| `MetalSrdShared` | 2 | `0x3087B3164` | 2 | `525cc8f73fc005eb` | `dfb779f77fa96bfb` | 2 | `0x11C4` |  |
| `MetalSrdShared` | 3 | `0x3087F0771` | 456 | `71cf0143fe4fe704` | `e0c27a0fe7f91698` | 3 | `0x0` |  |
| `MetalSrdShared` | 0 | `0x30886C85C` | 215 | `54ebf2cfa0983975` | `01baca80019cab60` | 0 | `0x0` |  |
| `MetalSrdShared` | 1 | `0x30886C933` | 10 | `580a055bd41ed3f1` | `5497793b124348f1` | 1 | `0x0` |  |
| `MetalSrdShared` | 2 | `0x3087B3164` | 2 | `525cc8f73fc005eb` | `dfb779f77fa96bfb` | 2 | `0x11C4` |  |
| `MetalSrdShared` | 3 | `0x3087F0771` | 456 | `71cf0143fe4fe704` | `e4529baf973efc50` | 3 | `0x0` |  |
| `MetalSrdShared` | 0 | `0x310D71A28` | 215 | `886322b1d50af710` | `8e8b5401b13b4cd2` | 4294967295 | `0x0` |  |
| `MetalSrdShared` | 1 | `0x310D71AFF` | 10 | `8944785f90985578` | `5497793b124348f1` | 4294967295 | `0x0` |  |
| `MetalLegacyBlend` | 0 | `0x30354D1BC` | 2 | `525cc8f73fc005eb` | `dfb779f77fa96bfb` | 4294967295 | `0x0` | 0x30354D000+0x1BC |
| `MetalLegacyBlend` | 0 | `0x310D71A28` | 215 | `886322b1d50af710` | `8e8b5401b13b4cd2` | 4294967295 | `0x0` |  |
| `MetalLegacyBlend` | 1 | `0x310D71AFF` | 10 | `8944785f90985578` | `5497793b124348f1` | 4294967295 | `0x0` |  |
| `MetalLegacyBlend` | 2 | `0x310C77708` | 2 | `71e11446bbd6b2c8` | `182003d5c37dc525` | 4294967295 | `0x0` |  |
| `MetalPipelineConstructor` | 0 | `0x0` | 2 | `71e11446bbd6b2c8` | `182003d5c37dc525` | 0 | `0x101A` |  |
| `MetalSubmitGuard` | 0 | `0x30355E210` | 15 | `e988027e1805ae2b` | `846cf88f544efa74` | 4294967295 | `0x0` |  |
| `MetalSubmitGuard` | 0 | `0x3087C3580` | 15 | `e988027e1805ae2b` | `846cf88f544efa74` | 4294967295 | `0x0` |  |
| `MetalSubmitGuard` | 0 | `0x310CE7240` | 15 | `e988027e1805ae2b` | `846cf88f544efa74` | 4294967295 | `0x0` |  |

OpenGL 相对引用（原生/已修复）：

- `OpenGLHwInfo/Darwin23/0x15D8`：原生 [332→0xF0520, 407→0xF0510, 468→0xF0510]；已修复 [342→0xF0520, 401→0xF0510, 443→0xF0510]
- `OpenGLHwInfo/Darwin23/0x15DD`：原生 [332→0xF0520, 407→0xF0510, 468→0xF0510]；已修复 [342→0xF0520, 401→0xF0510, 443→0xF0510]
- `OpenGLHwInfo/Darwin23/0x15E7`：原生 [332→0xF0520, 407→0xF0510, 468→0xF0510]；已修复 [342→0xF0520, 401→0xF0510, 443→0xF0510]
- `OpenGLHwInfo/Darwin23/0x1636`：原生 [332→0xF0520, 407→0xF0510, 468→0xF0510]；已修复 [342→0xF0520, 401→0xF0510, 443→0xF0510]
- `OpenGLHwInfo/Darwin23/0x1638`：原生 [332→0xF0520, 407→0xF0510, 468→0xF0510]；已修复 [342→0xF0520, 401→0xF0510, 443→0xF0510]
- `OpenGLHwInfo/Darwin23/0x164C`：原生 [332→0xF0520, 407→0xF0510, 468→0xF0510]；已修复 [342→0xF0520, 401→0xF0510, 443→0xF0510]
- `OpenGLHwInfo/Darwin24/0x15D8`：原生 [332→0xEDEE0, 407→0xEDED0, 468→0xEDED0]；已修复 [342→0xEDEE0, 401→0xEDED0, 443→0xEDED0]
- `OpenGLHwInfo/Darwin24/0x15DD`：原生 [332→0xEDEE0, 407→0xEDED0, 468→0xEDED0]；已修复 [342→0xEDEE0, 401→0xEDED0, 443→0xEDED0]
- `OpenGLHwInfo/Darwin24/0x15E7`：原生 [332→0xEDEE0, 407→0xEDED0, 468→0xEDED0]；已修复 [342→0xEDEE0, 401→0xEDED0, 443→0xEDED0]
- `OpenGLHwInfo/Darwin24/0x1636`：原生 [332→0xEDEE0, 407→0xEDED0, 468→0xEDED0]；已修复 [342→0xEDEE0, 401→0xEDED0, 443→0xEDED0]
- `OpenGLHwInfo/Darwin24/0x1638`：原生 [332→0xEDEE0, 407→0xEDED0, 468→0xEDED0]；已修复 [342→0xEDEE0, 401→0xEDED0, 443→0xEDED0]
- `OpenGLHwInfo/Darwin24/0x164C`：原生 [332→0xEDEE0, 407→0xEDED0, 468→0xEDED0]；已修复 [342→0xEDEE0, 401→0xEDED0, 443→0xEDED0]
- `OpenGLHwInfo/Darwin25/0x15D8`：原生 [332→0xED420, 407→0xED410, 468→0xED410]；已修复 [342→0xED420, 401→0xED410, 443→0xED410]
- `OpenGLHwInfo/Darwin25/0x15DD`：原生 [332→0xED420, 407→0xED410, 468→0xED410]；已修复 [342→0xED420, 401→0xED410, 443→0xED410]
- `OpenGLHwInfo/Darwin25/0x15E7`：原生 [332→0xED420, 407→0xED410, 468→0xED410]；已修复 [342→0xED420, 401→0xED410, 443→0xED410]
- `OpenGLHwInfo/Darwin25/0x1636`：原生 [332→0xED420, 407→0xED410, 468→0xED410]；已修复 [342→0xED420, 401→0xED410, 443→0xED410]
- `OpenGLHwInfo/Darwin25/0x1638`：原生 [332→0xED420, 407→0xED410, 468→0xED410]；已修复 [342→0xED420, 401→0xED410, 443→0xED410]
- `OpenGLHwInfo/Darwin25/0x164C`：原生 [332→0xED420, 407→0xED410, 468→0xED410]；已修复 [342→0xED420, 401→0xED410, 443→0xED410]
- `OpenGLDualQuad/Darwin23/0x15D8`：原生 [84→0x612E8]；已修复 [87→0x612E8]
- `OpenGLDualQuad/Darwin23/0x15DD`：原生 [84→0x612E8]；已修复 [87→0x612E8]
- `OpenGLDualQuad/Darwin23/0x15E7`：原生 [84→0x612E8]；已修复 [87→0x612E8]
- `OpenGLDualQuad/Darwin23/0x1636`：原生 [84→0x612E8]；已修复 [87→0x612E8]
- `OpenGLDualQuad/Darwin23/0x1638`：原生 [84→0x612E8]；已修复 [87→0x612E8]
- `OpenGLDualQuad/Darwin23/0x164C`：原生 [84→0x612E8]；已修复 [87→0x612E8]
- `OpenGLDualQuad/Darwin24/0x15D8`：原生 [84→0x5FEA4]；已修复 [87→0x5FEA4]
- `OpenGLDualQuad/Darwin24/0x15DD`：原生 [84→0x5FEA4]；已修复 [87→0x5FEA4]
- `OpenGLDualQuad/Darwin24/0x15E7`：原生 [84→0x5FEA4]；已修复 [87→0x5FEA4]
- `OpenGLDualQuad/Darwin24/0x1636`：原生 [84→0x5FEA4]；已修复 [87→0x5FEA4]
- `OpenGLDualQuad/Darwin24/0x1638`：原生 [84→0x5FEA4]；已修复 [87→0x5FEA4]
- `OpenGLDualQuad/Darwin24/0x164C`：原生 [84→0x5FEA4]；已修复 [87→0x5FEA4]
- `OpenGLDualQuad/Darwin25/0x15D8`：原生 [84→0x5F5B2]；已修复 [87→0x5F5B2]
- `OpenGLDualQuad/Darwin25/0x15DD`：原生 [84→0x5F5B2]；已修复 [87→0x5F5B2]
- `OpenGLDualQuad/Darwin25/0x15E7`：原生 [84→0x5F5B2]；已修复 [87→0x5F5B2]
- `OpenGLDualQuad/Darwin25/0x1636`：原生 [84→0x5F5B2]；已修复 [87→0x5F5B2]
- `OpenGLDualQuad/Darwin25/0x1638`：原生 [84→0x5F5B2]；已修复 [87→0x5F5B2]
- `OpenGLDualQuad/Darwin25/0x164C`：原生 [84→0x5F5B2]；已修复 [87→0x5F5B2]

## 7. 实机验证边界

- 当前版本尚未完成实机启动和图形工作负载验收。

## 8. 新旧计划对照基座

- 共享缓存：`tools/vpf/plan_probe.cpp` + `tools/vpf/compare_plan.py` +
  `tools/vpf/fixtures/baseline.json`。
- OpenGL 页计划：`tools/dvpf/display_plan_probe.cpp` + `tools/dvpf/compare_display_plan.py`。
- 清单与生成头：`python3 tools/dvpf/generate_display_manifest.py --check`。
- 逐项等价：`python3 tools/test_display_plan_equivalence.py`；字节、地址、函数引用和状态必须一致。

## 9. 验证命令

```sh
python3 tools/build_opengl_patch.py --check
python3 tools/vpf/generate_manifest.py --check
python3 tools/vpf/validate_manifest.py
python3 tools/dvpf/generate_display_manifest.py --check
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
python3 tools/test_vpf_manifest.py
python3 tools/test_vpf_plan.py
python3 tools/test_vpf_baseline.py
python3 tools/test_vpf_feature_state.py
python3 tools/test_vpf_lifecycle.py
```
