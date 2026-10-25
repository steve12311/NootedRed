"""验证当前校验页内定位、rel32 重建、歧义拒绝及真实 15/26 驱动。"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/opengl/dynamic'


def main():
    stubs = WORK / 'stubs/IOKit'
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    harness = r'''
#include <array>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <vector>
#include <UserOpenGLResolver.hpp>
using namespace UserOpenGLResolver;
static std::array<UInt8,4096> moved(const UserOpenGLData::Device& d,unsigned kind,UInt64 page,unsigned offset,bool patched=false){
 std::array<UInt8,4096> bytes;bytes.fill(0xcc);
 const auto& patch=d.patches[kind];const auto& fn=d.resolver->functions[kind];
 const auto start=d.resolver->locations[kind].offset;
 memcpy(bytes.data()+offset,patched?patch.patched:patch.original,patch.size);
 const auto* refs=patched?fn.patchedRefs:fn.originalRefs;auto count=patched?fn.patchedCount:fn.originalCount;
 for(unsigned r=0;r<count;r++)assert(UserSharedCache::setRelative(bytes.data()+offset,refs[r].offset-start,page+offset,refs[r].rva));
 return bytes;
}
int main(int argc,char** argv){
 unsigned cases=0;
 for(const auto& d:UserOpenGLData::Devices)for(unsigned k=0;k<2;k++){
  const auto& p=d.pages[k];const auto& fn=d.resolver->functions[k];const auto& patch=d.patches[k];
  const auto start=d.resolver->locations[k].offset;
  const UInt64 referencePage=patch.offset-p.offset;
  Resolved plan{};std::array<UInt8,4096> bytes;memcpy(bytes.data(),p.original,4096);
  assert(resolvePage(d,bytes.data(),referencePage,k,plan)&&!plan.dynamic&&!plan.alreadyPatched);
  assert(plan.offset==p.offset&&plan.size==p.size&&memcmp(plan.patched,p.patched,p.size)==0);cases++;
  memcpy(bytes.data()+p.offset,p.patched,p.size);
  assert(resolvePage(d,bytes.data(),referencePage,k,plan)&&!plan.dynamic&&plan.alreadyPatched);cases++;
  // 穷举所有可完整容纳修复块的页内位置；页外没有任何输入可供读取。
  for(unsigned off=0;off<=4096-p.size;off++){
   const UInt64 page=0x40000;bytes=moved(d,k,page,off);const auto original=bytes;
   assert(resolvePage(d,bytes.data(),page,k,plan)&&plan.dynamic&&!plan.alreadyPatched&&plan.offset==off);
   assert(memcmp(plan.original,bytes.data()+off,p.size)==0);
   for(unsigned r=0;r<fn.patchedCount;r++){
    UInt64 target=0;auto& ref=fn.patchedRefs[r];
    assert(UserSharedCache::relativeTarget(page+off,ref.offset-start,plan.patched,target)&&target==ref.rva);
   }
   memcpy(bytes.data()+off,plan.patched,plan.size);
   assert(resolvePage(d,bytes.data(),page,k,plan)&&plan.alreadyPatched&&plan.dynamic);
   for(unsigned i=0;i<4096;i++)if(i<off||i>=off+p.size)assert(bytes[i]==original[i]);cases+=2;
  }
  // 每个非 rel32 字节的未知实现都必须拒绝；每个 rel32 字段须指向已知目标 RVA。
  for(unsigned i=0;i<p.size;i++){
   bytes=moved(d,k,0x40000,500);bytes[500+i]^=1;
   assert(!resolvePage(d,bytes.data(),0x40000,k,plan));cases++;
  }
  // 原生、已修复或两者混合的重复候选都不能放行。
  for(unsigned variant=0;variant<3;variant++){
   bytes=moved(d,k,0x40000,100,variant==1);
   auto other=moved(d,k,0x40000,1000,variant!=0);
   memcpy(bytes.data()+1000,other.data()+1000,p.size);
   assert(!resolvePage(d,bytes.data(),0x40000,k,plan));cases++;
  }
  // 无法完整容纳的块不能跨页拼接；ASan 检查页末读取边界。
  auto complete=moved(d,k,0x40000,0);
  for(unsigned remaining=1;remaining<p.size;remaining++){
   bytes.fill(0xcc);memcpy(bytes.data()+4096-remaining,complete.data(),remaining);
   assert(!resolvePage(d,bytes.data(),0x40000,k,plan));cases++;
  }
  bytes=moved(d,k,0x40000,500);
  assert(!resolvePage(d,nullptr,0x40000,k,plan));
  assert(!resolvePage(d,bytes.data(),0x40001,k,plan));
  assert(!resolvePage(d,bytes.data(),0xfffffffffffff000ULL,k,plan));
  assert(!resolvePage(d,bytes.data(),0x40000,2,plan));cases+=4;
 }
 for(int a=1;a<argc;a+=2){assert(a+1<argc);unsigned major=unsigned(atoi(argv[a]));
  std::ifstream stream(argv[a+1],std::ios::binary);assert(stream);std::vector<UInt8> file((std::istreambuf_iterator<char>(stream)),{});
  for(const auto& d:UserOpenGLData::Devices)if(d.major==major){
   unsigned found[2]={};
   for(unsigned offset=0;offset+4096<=file.size();offset+=4096)for(unsigned k=0;k<2;k++){
    Resolved p{};if(!resolvePage(d,file.data()+offset,offset,k,p))continue;
    assert(!p.alreadyPatched&&p.offset==d.pages[k].offset&&p.size==d.pages[k].size);
    assert(memcmp(p.patched,d.pages[k].patched,p.size)==0);found[k]++;
    // 改变补丁块外的字节，确认能脱离参考整页匹配。
    std::array<UInt8,4096> changed;memcpy(changed.data(),file.data()+offset,4096);
    changed[(p.offset+p.size)%4096]^=1;
    assert(resolvePage(d,changed.data(),offset,k,p)&&p.dynamic&&!p.alreadyPatched);cases++;
   }
   assert(found[0]==1&&found[1]==1);printf("PASS real page-local GL Darwin=%u PCI=%x\n",major,d.deviceID);cases++;
  }
 }
 printf("PASS %u page-local resolver cases\n",cases);
}
'''
    path, exe = WORK / 'test.cpp', WORK / 'test'
    path.write_text(harness)
    subprocess.run(['clang++', '-std=c++23', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    '-I'+str(WORK / 'stubs'), '-I'+str(ROOT / 'NootedRed'),
                    str(path), '-o', str(exe)], check=True, timeout=60)
    driver = 'System/Library/Extensions/AMDRadeonX5000GLDriver.bundle/Contents/MacOS/AMDRadeonX5000GLDriver'
    subprocess.run([str(exe), '25', '/'+driver, '24', '/Volumes/Macintosh SSD/'+driver], check=True, timeout=60)


if __name__ == '__main__':
    main()
