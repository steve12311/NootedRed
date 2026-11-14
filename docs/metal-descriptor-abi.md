# Experimental Darwin 27 / Sequoia AMD Metal descriptor compatibility

`tools/metal-compat` contains a source-only user-space adapter for a specific **interface pairing**: Darwin 27 Metal descriptor storage and a Sequoia AMD X5000 Metal driver. It does not select a laptop model, PCI device ID, SMBIOS, cache UUID, driver UUID or OS build number.

For the complete prepared-component workflow, start with the [graphics stack installation guide](darwin27-graphics-stack.md).

This is a component for experimental driver-transplant integrations. It does not make the ordinary NootedRed kext alone support Darwin 27, supply Apple binaries, load kernel collections or install a working graphics stack. Kernel driver matching, firmware, user-space dependency reconstruction and any existing command-buffer layout adaptation remain the integrator's responsibility. The existing supported-OS kernel paths are unchanged.

## Failure and evidence

A transplanted driver can create a Metal device, compile shaders, create a render pipeline and complete commands, yet leave every pixel at the render pass's clear color. Compute dispatch can work while the desktop remains black with a hardware cursor.

The AMD driver obtains `_descriptorPrivate` and interprets its packed fields using the older layout. Getter disassembly of the two system frameworks established these differences:

| Field | Sequoia consumer | Darwin 27 producer |
| --- | --- | --- |
| Color write mask | bits 27–30 | bits 32–36 |
| Color pixel format | shift 36 | shift 42 |
| Blending enabled | one bit at 0 | two-bit state; public getter is true only for 1 |
| Rasterization enabled | bit 2 at private offset `0xe0` | bit 4 at the same offset |
| Render pipeline private storage | 632 bytes | 736 bytes |

For an RGBA8 target, the modern color word `0x1180f00002100` is read by the old driver as a **zero color write mask**. Translation produces `0x46078001080`, with mask 15 and pixel format 70. The observed pipeline word `0x1fe0010` translates to `0x7f8004`.

On a Sequoia host, a process-local test adapter first presented the newer packing to the same legacy driver. Shader compilation and command completion succeeded, but the triangle left red clear pixels unchanged. Converting that packing back restored all 16 green pixels. The original scoped implementation was then accepted on a Ryzen 5 5500U / Lucienne system running macOS 27.2 (26B123): desktop visible, triangle render/readback and 16-word compute/readback passed. That hardware/build identifies the evidence, not an activation restriction. The generalized implementation has repeated the host reproduction/repair and compute tests; it has not yet received a separate target reboot or a multi-device validation matrix.

## Activation and scope

The production library requires Darwin major 27 and verifies the framework interface before installing hooks:

- both descriptor classes and their private-storage ivars exist;
- storage sizes match the measured profile;
- getters point at the corresponding inline private storage;
- public setters produce the expected default and non-default color/blend samples, rasterization bit and alpha/topology fields.

Unknown layouts leave the adapter inactive. This deliberately does not infer support for a future ABI from an OS version alone.

Eligible caller images are identified by x86-64 architecture, macOS platform, a Sequoia minimum-OS major, and the canonical AMD X5000 Metal install name in their Mach-O load commands. The adapter uses the relocated `__TEXT` range, rather than one cache's addresses or UUID. Multiple distinct eligible images disable translation conservatively; removal clears the recorded range.

The two `_descriptorPrivate` implementations return translated storage **only to calls originating in the eligible AMD driver**. Modern Metal/framework callers receive the original pointer and bytes. The adapter does not modify the framework-owned descriptors. Each descriptor owns separate shadow storage per calling thread, preserving pointer lifetime and avoiding a shared scratch buffer across compiler threads. The legacy pipeline prefix is copied, then its packed flags are converted; a color descriptor is translated as an eight-byte word. Modern-only fields without a legacy representation are not exposed as invented legacy features.

`NRedMetalCompatStats` exposes color/pipeline translation counts. The first two conversions of each kind are written to stderr for bring-up diagnosis.

## Build and integration

From the repository root:

```sh
python3 tools/metal-compat/build.py test
python3 tools/metal-compat/build.py build
python3 tools/metal-compat/build.py build-probe
```

The first command runs CPU-only semantic/profile tests under AddressSanitizer and UndefinedBehaviorSanitizer. The other commands require macOS and build signed x86-64 artifacts under `build/metal-compat`. They do not install files, alter EFI, change launch jobs or restart anything.

The production dylib has install name `@rpath/NRedMetalCompat.dylib`. An integration must arrange to load it in each process using the transplanted AMD driver, with its dependencies and rpath resolvable. A dependency in a properly reconstructed donor image is one option. Do not blindly insert a load command into unknown header padding or distribute a modified Apple binary as part of this source contribution. Before installing Objective-C wrappers, the helper retains its own mapping with `RTLD_NODELETE`; closing a donor handle cannot leave dangling wrapper implementations.

A minimal GPU acceptance test is opt-in:

```sh
build/metal-compat/metal-probe --run
```

It selects an available AMD Metal device, runs a clear, compiles vertex/fragment shaders, draws a full-screen triangle to a 4×4 target, reads all 16 pixels, and dispatches a compute shader with 16-word readback. It reports no-AMD-device as skip (77), and bounds the worker to 90 seconds. It writes only stdout/stderr, not system logs. A successful probe does not establish broad application, OpenGL, video codec or sleep compatibility.

### Optional Sequoia reproduction

This test submits small GPU workloads on an AMD Metal device. Use a fresh test process for each mode; do not inject it into an existing desktop session.

```sh
python3 tools/metal-compat/build.py build-test
NRMC_TEST_MODE=1 DYLD_INSERT_LIBRARIES="$PWD/build/metal-compat/NRedMetalCompat-test.dylib" \
  build/metal-compat/metal-probe --run
NRMC_TEST_MODE=2 DYLD_INSERT_LIBRARIES="$PWD/build/metal-compat/NRedMetalCompat-test.dylib" \
  build/metal-compat/metal-probe --run
```

Mode 1 presents modern packing to the old driver (expected triangle failure, exit 23); mode 2 translates it back (expected rendering/compute success). The test build requires Darwin 24 and checks its legacy descriptor profile. Production builds have neither this opt-in nor a way to activate on Sequoia.

## Remaining work

- Validate the generalized source build on Darwin 27 independently of the initial scoped implementation.
- Add other GPU/driver combinations through actual validation, not PCI-ID guesses.
- Audit additional private interfaces if complex rendering exposes another mismatch.
- Keep build/install orchestration in the transplant integration; this adapter should not acquire machine-specific deployment responsibilities.

This contribution was developed with AI assistance. The live rendering and compute results above were measured on hardware; untested combinations are explicitly identified.
