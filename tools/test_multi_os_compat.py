"""执行真实版本入口、原始页面核验和设备识别，不提交 GPU 命令。

历史 Video Profile 与 Darwin 24 Metal 只从离线头读取，仅证明参考数据可复现；
生产头只能提供 Darwin 23 Metal。
"""
from pathlib import Path
import subprocess

from import_legacy_compat import write_fixture

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/multi-os-compat'


def main():
    stubs = WORK / 'stubs/IOKit'
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    util = WORK / 'stubs/Headers'
    util.mkdir(parents=True, exist_ok=True)
    (util / 'kern_util.hpp').write_text('#pragma once\n#include <IOKit/IOTypes.h>\n')
    src = r'''
#include <array>
#include <cassert>
#include <cstdio>
#include <map>
#include <vector>
#include <UserLegacyVideoFixture.hpp>
#include <VCNCapabilities.hpp>
using namespace UserLegacyCompat;
static constexpr uint64_t base=0x7ff800100000ULL;
static std::map<uint64_t,std::array<uint8_t,4096>> memory;
static unsigned reads=0;
static int reader(uint64_t a,void* p,size_t n){
 reads++;auto i=memory.find(a&~uint64_t(4095));
 if(i==memory.end()||n>4096-(a&4095))return 1;
 memcpy(p,i->second.data()+(a&4095),n);return 0;
}
int main(){
 for(unsigned major:{23U,24U,25U})for(unsigned minor:{0U,1U,6U,99U}){
  const PenguinWizardry::KernelVersion v(major,minor);
  assert(VCNCapabilities::rejection(0x1638,v)==0);
  assert(VCNCapabilities::rejection(0x15DD,v)==5);
  assert(VCNCapabilities::rejection(0xFFFF1638,v)==3);
 }
 for(unsigned major:{22U,26U})assert(VCNCapabilities::rejection(0x1638,PenguinWizardry::KernelVersion(major,0))==4);
 for(unsigned major:{23U,24U})for(unsigned device:{0x15E7U,0x1636U,0x1638U,0x164CU}){
  for(const auto* profile:{UserLegacyFixture::metal(major),UserLegacyFixture::video(major,device)}){
   assert(profile);memory.clear();reads=0;
   for(unsigned i=0;i<profile->pageCount;i++)memcpy(memory[base+profile->pages[i].offset].data(),profile->pages[i].original,4096);
   assert(matches(reader,base,profile->uuid,*profile));assert(reads==16*profile->pageCount);
   auto uuid=std::array<uint8_t,16>{};assert(!matches(reader,base,uuid.data(),*profile));
   auto& first=memory.begin()->second;first[0]^=1;assert(!matches(reader,base,profile->uuid,*profile));first[0]^=1;
   for(unsigned i=0;i<profile->patchCount;i++){
    const auto& p=profile->patches[i];auto& page=memory.at((base+p.offset)&~uint64_t(4095));
    assert((p.offset&4095)+p.size<=4096&&memcmp(page.data()+(p.offset&4095),p.original,p.size)==0);
    memcpy(page.data()+(p.offset&4095),p.patched,p.size);
   }
   assert(matches(reader,base,profile->uuid,*profile));
   memory.erase(memory.begin());assert(!matches(reader,base,profile->uuid,*profile));
  }
 }
 assert(!UserLegacyFixture::video(25,0x1638)&&!UserLegacyFixture::video(23,0x15DD)&&!UserLegacyFixture::metal(26));
 // 生产头只保留 Darwin 23 Metal；Darwin 24 走动态解析，历史 Video Profile 不进入生产数据。
 assert(UserLegacyData::metal(23)==&UserLegacyData::OS23Metal&&UserLegacyFixture::metal(23)==UserLegacyData::metal(23));
 assert(!UserLegacyData::metal(24)&&!UserLegacyData::metal(25));
 assert(UserLegacyData::submitOffset(23)==0x30355E210ULL&&UserLegacyData::submitOffset(24)==0x3087C3580ULL&&!UserLegacyData::submitOffset(25));
 puts("PASS: Darwin 23/24/25 entry; historical reference profiles reproduce exact UUID/full pages; production header limited to Darwin 23 Metal");
}
'''
    fixture = write_fixture()
    source = WORK / 'test.cpp'
    source.write_text(src)
    exe = WORK / 'test'
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-I'+str(WORK / 'stubs'), '-I'+str(ROOT / 'NootedRed'), '-I'+str(fixture), str(source), '-o', str(exe)],
                   check=True, timeout=60)
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
