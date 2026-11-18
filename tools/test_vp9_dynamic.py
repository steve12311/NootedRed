"""VP9 动态配置：未知 UUID、函数和数据节重排、ABI 拒绝。"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/vp9/dynamic'


def main():
    stubs = WORK / 'stubs/IOKit'
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    fixture = (ROOT / 'tools/fixtures/user-cache-dynamic.hpp').read_text()
    source = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <vector>
#include <UserVP9DecodeResolver.hpp>
static std::map<std::string,unsigned> properties;
''' + fixture + r'''
#include "vp9-cache-dynamic.hpp"
int main(){
 static UserVP9DecodeResolver::Plan plan;
 uint8_t scan[UserSharedCache::Input::ScanSize];
 const auto check=[&](unsigned major,const auto& images,const auto& functions,const auto& sections,
                     const auto& slots,const auto& patches){
  const auto prepare=[&]{vp9Cache(images,functions,sections,slots);};
  prepare();auto original=blocks;
  UserSharedCache::Input input{reader,{}};input.scanBuffer=scan;
  assert(UserVP9DecodeResolver::resolve(input,base,plan,major));
  assert(blocks==original&&writes==0&&protects==0);
  assert(plan.count==std::size(patches));
  for(unsigned i=0;i<plan.count;i++){
   const auto& p=plan.patches[i];uint8_t bytes[MaxPatchSize];
   assert(reader(base+p.offset,bytes,p.size)==0&&!memcmp(bytes,p.original,p.size));
  }
  // UUID 不参与放行，完整 ABI 及所有函数仍须匹配。
  uint8_t otherUUID[16]={};put(base+88,otherUUID,16);
  UserSharedCache::Input unknown{reader,{}};unknown.scanBuffer=scan;
  assert(UserVP9DecodeResolver::resolve(unknown,base,plan,major));
  auto address=functionAddresses[slots[0].constructor]+slots[0].field;
  uint8_t field[4];uint64_t table;assert(reader(address,field,4)==0);
  assert(UserCacheResolver::relativeTarget(address,0,field,table));
  word64(table+slots[0].slot,0);
  UserSharedCache::Input badABI{reader,{}};badABI.scanBuffer=scan;
  assert(!UserVP9DecodeResolver::resolve(badABI,base,plan,major));
  prepare();uint8_t corrupt=0xCC;
  put(functionAddresses.back()+functions[std::size(functions)-1].size-1,&corrupt,1);
  UserSharedCache::Input badCode{reader,{}};badCode.scanBuffer=scan;
  assert(!UserVP9DecodeResolver::resolve(badCode,base,plan,major));
  printf("PASS VP9 dynamic Darwin %u dispatch: moved functions/sections, unknown UUID, ABI/code rejection\n",major);
 };
 namespace Sonoma=UserVP9DecodeSonomaData;
 namespace Sequoia=UserVP9DecodeSequoiaData;
 check(23,Sonoma::ResolverImages,Sonoma::ResolverFunctions,Sonoma::SectionTargets,Sonoma::ABISlots,Sonoma::Patches);
 check(24,Sequoia::ResolverImages,Sequoia::ResolverFunctions,Sequoia::SectionTargets,Sequoia::ABISlots,Sequoia::Patches);
 check(25,ResolverImages,ResolverFunctions,SectionTargets,ABISlots,Patches);
 for(unsigned major:{22U,26U,27U}){
  UserSharedCache::Input input{reader,{}};input.scanBuffer=scan;
  assert(!UserVP9DecodeResolver::resolve(input,base,plan,major)&&input.reads==0);
 }
}
'''
    file, exe = WORK / 'test.cpp', WORK / 'test'
    file.write_text(source)
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-O1', '-fsanitize=address,undefined',
                    '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'), '-I' + str(ROOT / 'tools/fixtures'), str(file), '-o', str(exe)],
                   check=True, timeout=60)
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
