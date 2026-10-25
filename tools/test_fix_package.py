"""检查最终 kext 的三个新参数，以及撤回参数和 HDR 候选是否残留。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OLD_FLAGS = [
    '-NRedVCN', '-NRedLegacyBlend', '-NRedImmediateSync', '-NRedSrdShared',
    '-NRedComputeScratch', '-NRedTransfer1x1', '-NRedHDRIntegerFilter', '-NRedNoSubmitGuard',
    '-NRedGLFix', '-NRedGLFixOff', '-NRedGLFixResolve', '-NRedGLFixResolveOnly', '-NRedGLFixResolveOff',
    '-NRedGLFixNoResolve',
]


def validate(kext):
    data = (kext / 'Contents/MacOS/NootedRed').read_bytes()
    for name in ['-NRedDisplayFix', '-NRedVideoFix', 'NRedECFix']:
        assert name.encode() + b'\0' in data, f'缺少新参数：{name}'
    for name in OLD_FLAGS:
        assert name.encode() + b'\0' not in data, f'残留旧参数：{name}'
    for marker in [b'NRedHDRFilter', b'HDRFilter-v1', b'UserHDRFilterData']:
        assert marker not in data, f'残留 HDR 候选：{marker.decode()}'


def main():
    surface = (ROOT / 'NootedRed/UserSurfaceSync.cpp').read_text()
    assert 'HDR' not in surface
    for path in ['UserSurfaceSync.cpp', 'UserVideoDecode.cpp', 'UserOpenGL.cpp', 'VCN.cpp']:
        source = (ROOT / 'NootedRed' / path).read_text()
        for name in OLD_FLAGS:
            assert f'checkKernelArgument("{name}")' not in source, f'{path} 仍解析 {name}'
    gl = (ROOT / 'NootedRed/UserOpenGL.cpp').read_text()
    assert 'checkKernelArgument("-NRedDisplayFix")' in gl
    assert 'UserOpenGL::init(patcher)' in (ROOT / 'NootedRed/NRed.cpp').read_text()
    ec = (ROOT / 'NootedRed/ECBacklight.cpp').read_text()
    assert 'PE_parse_boot_argn("NRedECBacklight"' not in ec
    for path in sys.argv[1:]:
        validate(Path(path))
    print('PASS three new boot arguments; no legacy fix parsers or HDR candidate in checked kexts')


if __name__ == '__main__':
    main()
