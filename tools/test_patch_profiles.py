"""验证 Darwin 23/24/25 的公共解析流程、FVRC 功能组与失败拒绝。"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/patch-profiles'


def main():
    stubs = WORK / 'stubs/IOKit'
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / 'IOTypes.h').write_text(
        '#pragma once\n#include <cstdint>\n'
        'using UInt8=uint8_t;using UInt16=uint16_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    source = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <vector>
#include <UserSurfaceSyncResolver.hpp>
#include <UserVideoDecodeResolver.hpp>
static std::map<std::string,unsigned> properties;
#include "metal-dynamic-fixture.hpp"
namespace M=MetalFixture;
static bool dirty=false;
static bool probe(void*,uint64_t address,uint32_t depth,VPF::MappingInfo& info){
 for(unsigned i=0;i<M::imageAddresses.size();i++){
  const auto start=M::imageAddresses[i]+0x1000,end=start+0x6f000;
  if(start<=address&&address<end){
   info={address,end-address,depth,0x1234U+i,uint8_t(dirty?2:5),0,false,false};return true;
  }
 }
 return false;
}
static VPF::FastResolution begin(unsigned major){
 static uint8_t uuid[16];assert(M::reader(M::base+88,uuid,sizeof(uuid))==0);
 VPF::FastResolution fast;fast.begin(uuid,major,VPF::resolutionProfile(0x1638,major),0,probe,nullptr);return fast;
}
static bool surface(unsigned major,UserSurfaceSyncResolver::Plan& plan,VPF::FastResolution& fast){
 plan.input={M::reader,{}};plan.input.scanBuffer=plan.scan;
 return UserSurfaceSyncResolver::resolveMetal(plan.input,M::base,0x1638,plan,major,&fast);
}
static void checkGroups(unsigned major){
 bool core=false,scratch=false,constructor=false;
 for(auto&entry:VPF::resolutionCache.entries){
  if(entry.state!=1||entry.value.key.darwinMajor!=major)continue;
  const auto group=entry.value.key.functionGroupId;
  if(group==unsigned(VPF::FunctionGroup::DisplayCore)){
   assert(entry.value.imageCount==1&&entry.value.functionCount>=3&&entry.value.functionCount<=4);core=true;
  }
  if(group==unsigned(VPF::FunctionGroup::DisplayConstructor)){
   assert(entry.value.functionCount==1);constructor=true;
  }
  if(group==unsigned(VPF::FunctionGroup::DisplayScratch)){
   assert(entry.value.functionCount==1);scratch=true;
  }
 }
 assert(core&&scratch&&(major==25||constructor));
}
int main(){
 VPF::ResolutionDiagnostics::setEnabled(true);
 for(unsigned major:{23U,24U,25U}){
  VPF::resolutionCache={};VPF::resolutionCache.init();dirty=false;M::setupOS(0x1638,major);
  UserSurfaceSyncResolver::Plan cold{};auto first=begin(major);assert(surface(major,cold,first));checkGroups(major);
  UserSurfaceSyncResolver::Plan hot{};auto second=begin(major);assert(surface(major,hot,second));
  assert(hot.reads<cold.reads);
  for(unsigned i=0;i<4;i++){
   assert(cold.patches[i].offset==hot.patches[i].offset&&cold.patches[i].size==hot.patches[i].size);
   assert(memcmp(cold.patches[i].original,hot.patches[i].original,cold.patches[i].size)==0);
   assert(memcmp(cold.patches[i].patched,hot.patches[i].patched,cold.patches[i].size)==0);
  }
  dirty=true;UserSurfaceSyncResolver::Plan fallback{};auto third=begin(major);
  assert(surface(major,fallback,third)&&fallback.reads>=hot.reads);dirty=false;
  const auto constant=M::imageAddresses[0]+0x71064;M::word32(constant,0x40000000);
  UserSurfaceSyncResolver::Plan bad{};auto fourth=begin(major);assert(!surface(major,bad,fourth));
  assert(M::writes==0&&M::protects==0);
  printf("PASS Darwin%u: shared Metal core/scratch FVRC groups, hot replay, dirty fallback and constant rejection\n",major);
  if(major<25){
   M::setupOS(0x1638,major);
   const auto& functions = major==23 ? UserSonomaSurfaceData::ResolverFunctions
                                     : UserSequoiaSurfaceData::ResolverFunctions;
   const auto& ref = functions[4].originalRefs[0];
   const auto target = M::destination(ref,functions,std::size(functions));
   M::rel(M::functionAddresses[4],ref.offset,target+4);
   UserSurfaceSyncResolver::Plan wrongBinding{};auto proof=begin(major);
   assert(!surface(major,wrongBinding,proof)&&M::writes==0&&M::protects==0);
  }
  assert(UserVideoDecodeResolver::findDevice(0x1638,major));
  assert(!UserVideoDecodeResolver::findDevice(0x15DD,major));
  assert(UserVideoDecodeResolver::fairPlayMask(major)!=0);
 }
 assert(!UserPatchProfiles::dispatch(22,[](auto){return true;}));
 assert(!UserPatchProfiles::dispatch(26,[](auto){return true;}));
 puts("PASS common profiles: unsupported systems reject; shared execution groups");
}
'''
    path, exe = WORK / 'test.cpp', WORK / 'test'
    path.write_text(source)
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-O2', '-fsanitize=address,undefined',
                    '-fno-omit-frame-pointer', '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'),
                    '-I' + str(ROOT / 'tools/fixtures'), str(path), '-o', str(exe)], check=True, timeout=60)
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
