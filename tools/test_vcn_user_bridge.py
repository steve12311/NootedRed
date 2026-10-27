"""执行实际视频桥接和守卫解析器，验证整个 COW 事务；不访问 GPU。"""
from pathlib import Path
from user_patch_memory_harness import with_memory_helpers
import argparse
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--major',type=int,choices=[24,25],default=25)
    parser.add_argument('--sanitize',action='store_true')
    args=parser.parse_args()
    work=ROOT/'build/vcn-bridge'/str(args.major)
    stub=work/'stubs/IOKit';stub.mkdir(parents=True,exist_ok=True)
    (stub/'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    (stub/'IOUserClient.h').write_text((ROOT/'tools/fixtures/fairplay-entitlement.hpp.in').read_text())
    for directory,name,content in [('libkern/c++','OSBoolean.h','#pragma once\n#include <IOKit/IOUserClient.h>\n'),('Headers','kern_util.hpp','#pragma once\n#include <IOKit/IOTypes.h>\n')]:
        path=work/'stubs'/directory;path.mkdir(parents=True,exist_ok=True);(path/name).write_text(content)
    source=(ROOT/'NootedRed/UserVideoDecode.cpp').read_text()
    body=re.split(r'namespace\s*\{',source,maxsplit=1)[1].split('}    // namespace',1)[0]
    preamble=(ROOT/'tools/fixtures/vcn-user-bridge.cpp.in').read_text().split('static bool isWindowServer')[0]
    template=(ROOT/'tools/fixtures/vcn-guard-bridge.cpp.in').read_text()
    if args.major==24:
        template=template.replace('namespace D=UserVideoDecodeData;','namespace D=UserSequoiaVideoData;').replace('namespace G=UserVideoGuard25Data;','namespace G=UserVideoGuard24Data;').replace('unsigned major=25','unsigned major=24')
    # PatchCount 是各版本相同的补丁数量，旧生成头文件未单独导出常量。
    template=template.replace('D::PatchCount','std::size(D::Patches)')
    file=work/'test.cpp';exe=work/'test'
    file.write_text(preamble+template.replace('BODY',with_memory_helpers(body)).replace('INIT',source[source.index('void UserVideoDecode::init('):]))
    flags=['-fsanitize=address,undefined','-fno-omit-frame-pointer'] if args.sanitize else []
    subprocess.run(['xcrun','clang++','-std=c++23','-O2',*flags,'-I'+str(work/'stubs'),'-I'+str(ROOT/'NootedRed'),'-I'+str(ROOT/'tools/fixtures'),str(file),'-o',str(exe)],check=True,timeout=60)
    for device in ['15e7','1636','1638','164c']:
        for group in range(12 if device=='1638' else 11):
            print(f'BEGIN Darwin {args.major} device={device} group={group}',flush=True)
            subprocess.run([str(exe),device,str(group)],check=True,timeout=60)


if __name__=='__main__':main()
