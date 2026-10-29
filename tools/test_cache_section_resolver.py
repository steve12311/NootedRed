"""在 CPU 模拟缓存中检验节重排、构造函数重定位和真实指令／目标拒绝。"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/cache-section-targets/regression'


def main():
    stub = WORK / 'stubs/IOKit'
    stub.mkdir(parents=True, exist_ok=True)
    (stub / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    source = r'''
#include <algorithm>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <vector>
#include <UserSurfaceSyncResolver.hpp>
static std::map<std::string,unsigned> properties;
#include "user-cache-dynamic.hpp"
namespace C=UserSurfaceConstructorData;
namespace S=UserSequoiaSurfaceData::Submit;
static void setup(){cache(C::ResolverImages,C::ResolverFunctions,true);}
static bool resolve(){
 UserSharedCache::Input input{reader,{}};UserSurfaceSyncResolver::Plan plan;
 bool ok=UserCacheResolver::resolve(input,base,C::ResolverImages,C::ResolverFunctions,C::Patches,
 C::ResolverLocations,plan.patches,&plan.original[0][0],&plan.patched[0][0],sizeof(plan.original[0]),1);
 assert(writes==0&&protects==0);
 if(ok){assert(plan.patches[0].offset+base==functionAddresses[0]+C::ResolverLocations[0].offset);
  assert(plan.patches[0].size==2&&plan.patches[0].original[0]==0x74&&plan.patches[0].patched[0]==0x90);}
 return ok;
}
static void submit(bool tahoe,bool buffered,unsigned kind){
 if(tahoe)cache(C::Submit::ResolverImages,C::Submit::ResolverFunctions,true);
 else cache(S::ResolverImages,S::ResolverFunctions);
 const auto& fn=tahoe?C::Submit::ResolverFunctions[0]:S::ResolverFunctions[0];
 auto expected=functionAddresses[0];
 if(kind==1||kind==7||kind==10||kind==11){
  const uint8_t jump[15]={0xff,0x25,0,0,0,0,1,2,3,4,5,6,7,8,0x90};
  put(expected,jump,sizeof(jump));if(kind==7){uint8_t bad=0;put(expected+14,&bad,1);}
 }
 if(kind==2||kind==3||kind==10||kind==11){
  const bool relocate=kind==2||kind==10;
  const auto moved=imageAddresses[0]+(relocate?0x1ff0:0x69000);
  std::vector<uint8_t> code(fn.size);assert(reader(expected,code.data(),code.size())==0);put(moved,code.data(),code.size());
  for(unsigned r=0;r<fn.originalCount;r++)rel(moved,fn.originalRefs[r].offset,destination(fn.originalRefs[r],&fn,1));
  if(relocate){uint8_t bad=0;put(expected,&bad,1);expected=moved;}
 }
 if(kind==4){uint8_t bad=0;put(expected,&bad,1);}
 if(kind==5){uint8_t bad=0;put(expected+15,&bad,1);}
 if(kind==6){auto ref=fn.originalRefs[0];rel(expected,ref.offset,destination(ref,&fn,1)+4);}
 if(kind==8)put(imageAddresses[0]+0x69000,fn.original,15);
 if(kind==9)failedRead=imageAddresses[0]+0x1000;
 UserSharedCache::Input input{reader,{}};uint8_t scan[UserSharedCache::Input::ScanSize];if(buffered)input.scanBuffer=scan;
 uint64_t offset=0;const bool ok=UserSurfaceSyncResolver::resolveSubmit(input,base,offset,tahoe);
 assert(ok==(kind==0||kind==1||kind==2||kind==8||kind==10));
 if(ok)assert(base+offset==expected);
 assert(writes==0&&protects==0);
}
int main(){
 setup();assert(resolve());unsigned cases=1;
 // 重排并跨页的完整函数保留真实引用；每个数据节独立放在不同地址。
 auto fn=C::ResolverFunctions[0];
 for(unsigned i=0;i<fn.size;i+=251){
  if(UserCacheResolver::skipped(i,fn.originalRefs,fn.originalCount))continue;
  setup();uint8_t byte=fn.original[i]^1;put(functionAddresses[0]+i,&byte,1);assert(!resolve());cases++;
 }
 // 所有重定位字段都必须指向正确目标，不能只掩掉变化字节。
 for(unsigned i=0;i<fn.originalCount;i++){
  setup();auto&ref=fn.originalRefs[i];uint8_t bytes[4];
  assert(reader(functionAddresses[0]+ref.offset,bytes,4)==0);uint64_t target=0;
  assert(UserSharedCache::relativeTarget(functionAddresses[0]+ref.offset,0,bytes,target));
  rel(functionAddresses[0],ref.offset,target+4);assert(!resolve());cases++;
 }
 // 共享缓存 UUID 不是兼容白名单；节身份和范围损坏仍必须拒绝。
 setup();uint8_t uuid[16];memset(uuid,0x5c,sizeof(uuid));put(base+88,uuid,sizeof(uuid));assert(resolve());cases++;
 setup();word32(imageAddresses[0]+20,65537);assert(!resolve());cases++;
 setup();failedRead=imageAddresses[0]+32;assert(!resolve());cases++;
 UserCacheResolver::ImageBounds bounds{};bounds.segmentCount=1;bounds.segments[0]={base,4096};
 assert(!bounds.contains(base,(uint64_t(1)<<32)+4));cases++;
 for(bool tahoe:{false,true})for(bool buffered:{false,true})for(unsigned kind=0;kind<12;kind++){
  submit(tahoe,buffered,kind);cases++;
 }
 // 扩展预筛选区内的所有固定字节仍须拒绝，包含已安装入口与跨缓冲区位置。
 for(bool tahoe:{false,true})for(bool installed:{false,true})for(unsigned byte=15;byte<32;byte++){
  if(tahoe)cache(C::Submit::ResolverImages,C::Submit::ResolverFunctions,true);
  else cache(S::ResolverImages,S::ResolverFunctions);
  const auto& fn=tahoe?C::Submit::ResolverFunctions[0]:S::ResolverFunctions[0];
  if(UserCacheResolver::skipped(byte,fn.originalRefs,fn.originalCount))continue;
  auto address=functionAddresses[0];
  if(installed){const uint8_t jump[15]={0xff,0x25,0,0,0,0,1,2,3,4,5,6,7,8,0x90};put(address,jump,15);}
  uint8_t bad=fn.original[byte]^1;put(address+byte,&bad,1);
  UserSharedCache::Input input{reader,{}};uint8_t scan[UserSharedCache::Input::ScanSize];input.scanBuffer=scan;
  uint64_t offset=0;assert(!UserSurfaceSyncResolver::resolveSubmit(input,base,offset,tahoe));cases++;
 }
 printf("PASS %u section-layout cases: independent data sections, moved constructor, UUID variation, every relocation target and non-relocation instruction rejection\n",cases);
}
'''
    file, executable = WORK / 'test.cpp', WORK / 'test'
    file.write_text(source)
    subprocess.run(['clang++', '-std=c++23', '-O2', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'),
                    '-I' + str(ROOT / 'tools/fixtures'), str(file), '-o', str(executable)], check=True, timeout=60)
    subprocess.run([str(executable)], check=True, timeout=60)


if __name__ == '__main__':
    main()
