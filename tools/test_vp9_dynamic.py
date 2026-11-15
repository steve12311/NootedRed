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
 for(unsigned major:{23U,24U,25U}){
  vp9Cache();auto original=blocks;
  UserSharedCache::Input input{reader,{}};input.scanBuffer=scan;
  assert(UserVP9DecodeResolver::resolve(input,base,plan,major));
  assert(blocks==original&&writes==0&&protects==0);
  for(unsigned i=0;i<UserVP9DecodeResolver::PatchCount;i++){
   const auto& p=plan.patches[i];uint8_t bytes[MaxPatchSize];
   assert(reader(base+p.offset,bytes,p.size)==0&&!memcmp(bytes,p.original,p.size));
  }
  // UUID 不参与放行，完整 ABI 及所有函数仍须匹配。
  uint8_t otherUUID[16]={};put(base+88,otherUUID,16);
  UserSharedCache::Input unknown{reader,{}};unknown.scanBuffer=scan;
  assert(UserVP9DecodeResolver::resolve(unknown,base,plan,major));
  auto address=functionAddresses[ABISlots[0].constructor]+ABISlots[0].field;
  uint8_t field[4];uint64_t table;assert(reader(address,field,4)==0);
  assert(UserCacheResolver::relativeTarget(address,0,field,table));
  word64(table+ABISlots[0].slot,0);
  UserSharedCache::Input badABI{reader,{}};badABI.scanBuffer=scan;
  assert(!UserVP9DecodeResolver::resolve(badABI,base,plan,major));
  vp9Cache();uint8_t corrupt=0xCC;
  put(functionAddresses.back()+ResolverFunctions[UserVP9DecodeResolver::FunctionCount-1].size-1,&corrupt,1);
  UserSharedCache::Input badCode{reader,{}};badCode.scanBuffer=scan;
  assert(!UserVP9DecodeResolver::resolve(badCode,base,plan,major));
  printf("PASS VP9 dynamic Darwin %u dispatch: moved functions/sections, unknown UUID, ABI/code rejection\n",major);
 }
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
