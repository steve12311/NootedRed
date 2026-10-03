"""VCN 候选不能把运行期可选的 Apple 驱动变成插件的加载前置条件。"""
import plistlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate(path):
    baseline = plistlib.loads((ROOT / "NootedRed/Info.plist").read_bytes())["OSBundleLibraries"]
    candidate = plistlib.loads(Path(path).read_bytes())["OSBundleLibraries"]
    assert candidate == baseline, f"候选改变了基线加载依赖: {candidate}"


if __name__ == "__main__":
    validate(sys.argv[1])
    print("候选依赖与渲染基线一致")
