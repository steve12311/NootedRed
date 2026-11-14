# NRedMetalCompat

Experimental, source-only adapter for Darwin 27 Metal descriptors consumed by Sequoia AMD X5000 drivers. Activation is based on interface profiles rather than hardware identifiers.

See [design, evidence, build and opt-in GPU tests](../../docs/metal-descriptor-abi.md).

`build.py` only builds or tests. There is no installer, bundled Apple driver, automatic GPU test, EFI change or restart.
