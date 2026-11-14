# Install the complete experimental Darwin 27 AMD graphics stack

**Copying NootedRed.kext alone is not sufficient.** The supported macOS 27.2 x86 bring-up image does not contain the older AMD kernel drivers, Metal driver or its compiler libraries. The prepared component package supplies those missing pieces. Users do not need to extract a dyld shared cache, locate individual dylibs or construct a kernel collection by hand.

[Download the prepared preview component package](https://github.com/hrx114514x/NootedRed/releases/tag/experimental-darwin27-stack-r1). This is a contributor preview, not an official upstream release. Download the ZIP and its SHA-256 file, verify the archive, and unpack it. The preview expands to about 7.1 GB because it contains six prebuilt Metal variants; allow additional space for the selected installation and rollback backups.

## What you need

- An already bootable **macOS 27.2 / 26B123 x86 NullMoth-style test installation**, with its original early-boot/WindowServer integration. This tool adds graphics; it is not a macOS installer or an ARM-to-x86 port.
- A working macOS installation from which to modify the **offline** test volume, with Python 3.9+ and `kmutil` (normally provided with the existing macOS/Command Line Tools environment).
- The test System/Data/Preboot volumes and your OpenCore EFI mounted. The tool discovers the paired Data and Preboot group from APFS metadata when available.
- A Darwin-27-capable Lilu entry in the base EFI. Existing CPU, networking and input support remain the responsibility of that boot environment.

The package's software profile is Sequoia **24H32** AMD donors and target **26B123**. These are software compatibility limits, not machine identity checks. Driver UUID checks identify common Apple binary revisions; volume UUIDs in receipts are generated from each recipient’s selected volumes, not a distributed machine whitelist. The six existing Metal patch variants are `15D8`, `15DD`, `15E7`, `1636`, `1638`, and `164C`; an unknown GPU is rejected instead of receiving the wrong patch. The original 164C implementation passed desktop, rendering and compute tests. Other devices and the newly packaged installer require their own native acceptance; listing a variant is not a claim that every associated laptop works.

## Install

Open **Install.command** in the unpacked package. Select the mounted test system and the OpenCore `EFI/OC` folder, then authenticate for the installation. The program checks the package, detects the GPU, prepares the recipient-specific collection and writes a rollback receipt. It does not restart the computer.

For a terminal workflow:

```sh
sudo python3 stack.py setup \
  --system '/Volumes/macOS 27 TEST' \
  --efi '/Volumes/OPENCORE/EFI/OC' \
  --work "$HOME/Library/Caches/NootedRed/graphics-preview" \
  --receipt "$HOME/Library/Application Support/NootedRed/graphics-preview"
```

If preparing a disk for a different computer, pass its GPU's `--device-id`; otherwise the tool reads the current machine's PCI registry. If volume discovery is ambiguous, supply `--data` and the target volume-group directory with `--preboot`. It never selects an ESP using a fixed `disk0`/`disk1` number.

For inspection without installation, use `doctor` with the same system/EFI arguments. `prepare --work ...` builds an inspectable `plan.json` without writing target files. Apply it with `install --plan .../plan.json --receipt ...`. The package, selected volumes and files must still match the plan when it is installed.

## Included components and actual work

| Layer | Supplied or generated |
| --- | --- |
| Kernel | NootedRed27; AMD ServiceManager, X5000, X5000HWServices/HWLibs and X6000Framebuffer |
| Metal | Rebuilt AMD X5000 Metal driver, selected from the existing GPU patch tables |
| Compiler | AMDShared, X5000Shared, X6000Shared, AMDNTPlugin, AMDIL902, SC and GCNASM |
| Compatibility | Runtime command-buffer ivar addressing and the Darwin 27 descriptor ABI adapter |
| Startup | Bounded auxiliary-collection loader wrapper and the existing bring-up kit's Apple graphics path |
| Acceptance | An opt-in/boot-time bounded shader rendering and compute readback probe |

The installer **relinks the AMD auxiliary collection against the recipient's own BootKC and SystemKC**. It does not distribute or replace another user's complete kernel collections. The existing unsigned BootKC receives only the narrow IONDRV PCI fallback metadata adjustment where required; its code, layout and UUID remain unchanged. The supplied SystemKC must match its Preboot copy and is not patched.

OpenCore receives a separate NootedRed27 entry limited to Darwin 27.2. The ordinary NootedRed entry remains available below Darwin 27. PlatformInfo/SMBIOS, global NVRAM arguments, CPU/ACPI, networking and input entries are preserved. Experimental graphics flags are written to the selected volume's boot plists, including `AMDBacklight=0`; backlight support is not claimed. The tool does not automatically disable a touchpad to work around an unrelated IOHID panic.

Preparation needs root ownership for `kmutil`'s staged bundles. It uses explicit input collections with `--allow-missing-kdk` and creates only an auxiliary collection in the work directory, so end users do not need to unpack or install a KDK for this step. The bundle IDs in the resulting collection are checked before a plan is emitted.

## Acceptance and rollback

Manually boot the test system and allow about three minutes for the bounded probe. Its log is:

```text
/var/log/nootedred-metal.log
```

A useful acceptance result is shader compilation, render pipeline creation, **16 correct triangle pixels** and **16 correct compute words**, plus a visible working desktop. A loaded kext, 2 GB VRAM report or a successfully written file is not proof of acceleration.

After returning to the working OS:

```sh
python3 stack.py verify --receipt '/path/to/receipt'
sudo python3 stack.py rollback --receipt '/path/to/receipt'
```

Keep the receipt directory. It contains the exact before/after hashes and backups. Installation journals each write and rolls back on copy errors; rollback refuses to overwrite later user edits. Volume identities and base collections are checked again before installation. An interrupted operation leaves a receipt and lock to inspect; do not delete either before determining whether another installation is still running.

No automatic reboot, sleep, BIOS change, log upload or account migration is performed. Broad application compatibility, video codecs, OpenGL and sleep remain unverified.

## Rebuilding a package (maintainers only)

Consumers use the prepared download. `pack.py` is for maintainers who already hold the existing reconstructed components; it is not an instruction for every user to extract Apple libraries.

`build_kext.py` builds the donor-capable kext from this repository using the initialized Lilu/MacKernelSDK submodules. `pack.py --inputs inputs.json --out package` validates kernel donor UUIDs, creates the six signed Metal variants from the existing unpatched reconstructed library, includes the shared/compiler bundles and runtime helpers, and emits a per-file manifest. A ZIP can be produced with `--zip` after the installer sources are final. No local input paths are put in the manifest.

The source repository contains tools, compatibility patches, profiles and documentation. The component archive is a separate preview artifact. Apple components retain their original notices; this source project's license does not relicense them. Open-source component license notices are included in the archive.
