"""验证三系统统一的设备准入、修复组和版本专属数据。"""
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/multi-os-compat'


def main():
    stubs = WORK / 'stubs/IOKit'
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt16=uint16_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    util = WORK / 'stubs/Headers'
    util.mkdir(parents=True, exist_ok=True)
    (util / 'kern_util.hpp').write_text('#pragma once\n#include <IOKit/IOTypes.h>\n')
    src = r'''
#include <array>
#include <cassert>
#include <cstdio>
#include <map>
#include <vector>
#include <UserSurfaceSyncResolver.hpp>
#include <UserVideoDecodeResolver.hpp>
#include <VPF/DisplayDependencyPlanner.hpp>
#include <VPF/PatchManifestData.hpp>
#include <VCNCapabilities.hpp>

int main(){
 for(unsigned major:{23U,24U,25U})for(unsigned minor:{0U,1U,6U,99U}){
  const PenguinWizardry::KernelVersion v(major,minor);
  assert(VCNCapabilities::rejection(0x1638,v)==0);
  assert(VCNCapabilities::rejection(0x15DD,v)==5);
  assert(VCNCapabilities::rejection(0xFFFF1638,v)==3);
 }
 for(unsigned major:{22U,26U})assert(VCNCapabilities::rejection(0x1638,PenguinWizardry::KernelVersion(major,0))==4);
 for(unsigned major:{23U,24U,25U}){
  assert(VPF::requestedGroupsForDisplayFix(true,major)==VPF::requestedGroupsForDisplayFix(true,25));
  assert(!VPF::requestedGroupsForDisplayFix(false,major));
  for(unsigned device:{0x15E7U,0x1636U,0x1638U,0x164CU}){
   const auto* manifest=VPF::ManifestData::find(VPF::FeatureId::Vcn2Decode,VPF::displayOsMask(major),device);
   assert(manifest && manifest->resolver.siteCount==23 && manifest->resolver.functionCount==19);
   const auto* patches=major==23?UserSonomaVideoData::findDevice(device):
                       major==24?UserSequoiaVideoData::findDevice(device):UserVideoDecodeData::findDevice(device);
   assert(patches);
  }
 }
 assert(!UserSonomaSurfaceData::findDevice(0x6860));
 assert(!UserSonomaVideoData::findDevice(0x15DD));
 puts("PASS: Darwin 23/24/25 version gates, common display groups, per-version full-function video data and device rejection");
}
'''
    source = WORK / 'test.cpp'
    source.write_text(src)
    exe = WORK / 'test'
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-I'+str(WORK / 'stubs'), '-I'+str(ROOT / 'NootedRed'), str(source), '-o', str(exe)],
                   check=True, timeout=60)
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
