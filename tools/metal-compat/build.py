#!/usr/bin/env python3
"""Build/test only. Never installs a library, edits EFI or restarts a service."""
import argparse
from pathlib import Path
import platform
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("action", choices=("test", "build", "build-test", "build-probe"))
parser.add_argument("--out", type=Path, default=ROOT.parents[1] / "build/metal-compat")
args = parser.parse_args()
if args.action == "test":
    with tempfile.TemporaryDirectory(prefix="nred-metal-abi-") as directory:
        executable = Path(directory) / "test-abi"
        subprocess.run(["clang", "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-fsanitize=address,undefined", str(ROOT / "test_descriptor_abi.c"),
                        "-o", str(executable)], check=True)
        subprocess.run([str(executable)], check=True)
else:
    if platform.system() != "Darwin":
        parser.error("The Objective-C runtime adapter must be built on macOS")
    args.out.mkdir(parents=True, exist_ok=True)
    if args.action == "build-probe":
        output = args.out / "metal-probe"
        subprocess.run(["clang", "-arch", "x86_64", "-mmacosx-version-min=15.0", "-fobjc-arc",
                        "-Wall", "-Wextra", "-Werror", "-framework", "Foundation", "-framework", "Metal",
                        "-framework", "IOKit", str(ROOT / "metal_probe.m"), "-o", str(output)], check=True)
        subprocess.run(["codesign", "--force", "--sign", "-", str(output)], check=True)
        print(output)
        raise SystemExit(0)
    name = "NRedMetalCompat" + ("-test" if args.action == "build-test" else "") + ".dylib"
    output = args.out / name
    command = ["clang", "-dynamiclib", "-arch", "x86_64", "-mmacosx-version-min=15.0",
               "-fobjc-arc", "-fvisibility=hidden", "-Wall", "-Wextra", "-Werror",
               "-framework", "Foundation", "-framework", "Metal",
               "-Wl,-install_name,@rpath/" + name, str(ROOT / "NRedMetalCompat.m"), "-o", str(output)]
    if args.action == "build-test":
        command.insert(1, "-DNRMC_TEST")
    subprocess.run(command, check=True)
    subprocess.run(["codesign", "--force", "--sign", "-", str(output)], check=True)
    print(output)
