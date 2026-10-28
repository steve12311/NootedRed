"""验证私有守卫页的动态定位、绝对绑定和重复安装；使用合成缓存，不访问 GPU。"""
from pathlib import Path
import subprocess
import argparse

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--major',type=int,choices=[24,25],default=25);p.add_argument('--sanitize',action='store_true');args=p.parse_args()
    work=ROOT/'build/vcn-guard-resolver'/str(args.major);stub=work/'stubs/IOKit';stub.mkdir(parents=True,exist_ok=True)
    (stub/'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    source=r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <map>
#include <vector>
#include <set>
#include <string>
#include <array>
#include <UserVideoGuardResolver.hpp>
static std::map<std::string,unsigned> properties;
#include "user-cache-dynamic.hpp"
using namespace UserVideoGuardMAJORData;
static constexpr unsigned major=MAJOR;
static constexpr auto count=sizeof(Entries)/sizeof(Entries[0]);
static constexpr auto functions=sizeof(ResolverFunctions)/sizeof(ResolverFunctions[0]);
static unsigned cases;
static void setup(){cache(ResolverImages,ResolverFunctions,true,SectionTargets,sizeof(SectionTargets)/sizeof(SectionTargets[0]));}
static bool resolve(UserVideoGuardResolver::Plan& plan){UserSharedCache::Input in{reader,{}};return UserVideoGuardResolver::resolve(in,base,major,plan);}
static int overlaySource(uint64_t address,void* bytes,size_t size){
 for(size_t i=0;i<size;i++)static_cast<uint8_t*>(bytes)[i]=uint8_t((address+i)*17);
 return 0;
}
static int overlayParent(void* context,uint64_t address,void* bytes,size_t size){
 ++*static_cast<unsigned*>(context);return overlaySource(address,bytes,size);
}
static int overlayFailure(void*,uint64_t,void*,size_t){return 42;}
static void installed(UserVideoGuardResolver::Plan& plan){
 assert(resolve(plan)&&!plan.installed);assert(UserVideoGuardResolver::setPage(plan,0x40000000,major));
 blocks[plan.page].resize(4096);put(plan.page,plan.expected,plan.length);
 for(unsigned i=0;i<plan.count;i++)put(base+plan.patches[i].offset,plan.patches[i].patched,plan.patches[i].size);
}
int main(){
 // 对照原逐字节语义，覆盖不相交、部分相交、交叠入口及零长度读取。
 UserVideoGuardResolver::Plan overlayPlan;overlayPlan.count=4;
 for(unsigned i=0;i<overlayPlan.count;i++){
  overlayPlan.patches[i].offset=16+i*19;overlayPlan.patches[i].size=32;
  for(unsigned j=0;j<32;j++)overlayPlan.original[i][j]=uint8_t(0x80+i*32+j);
 }
 unsigned parentCalls=0;
 UserVideoGuardResolver::Overlay overlay{overlaySource,overlayParent,&parentCalls,overlayPlan,0x1000};
 for(uint64_t address=0xfe0;address<0x1090;address++)for(size_t size=0;size<=96;size++){
  uint8_t expected[96],actual[96];assert(overlaySource(address,expected,size)==0);
  for(unsigned i=0;i<overlayPlan.count;i++)for(unsigned j=0;j<overlayPlan.patches[i].size;j++){
   const auto at=overlay.base+overlayPlan.patches[i].offset+j;
   if(at>=address&&at-address<size)expected[at-address]=overlayPlan.original[i][j];
  }
  assert(UserVideoGuardResolver::overlayReader(&overlay,address,actual,size)==0);
  assert(memcmp(expected,actual,size)==0);cases++;
 }
 assert(parentCalls==cases);overlay.parent=overlayFailure;
 uint8_t untouched=0x5a;assert(UserVideoGuardResolver::overlayReader(&overlay,0x1010,&untouched,1)==42&&untouched==0x5a);cases++;
 overlay.parent=nullptr;assert(UserVideoGuardResolver::overlayReader(&overlay,0x1010,&untouched,1)==0&&untouched==0x80);cases++;
 setup();UserVideoGuardResolver::Plan plan;assert(resolve(plan));assert(plan.count==count&&!plan.installed&&writes==0);cases++;
 for(const auto& b:Bindings){
  uint64_t actual;memcpy(&actual,plan.expected+b.offset,8);auto expected=functionAddresses[b.function]+b.value;
  if(b.reference){const auto& fn=ResolverFunctions[b.function];bool found=false;
   for(unsigned r=0;r<fn.originalCount;r++)if(fn.originalRefs[r].offset==b.value){expected=destination(fn.originalRefs[r],ResolverFunctions,functions);found=true;}
   assert(found);}
  assert(actual==expected);cases++;
 }
 // 安装后再次完整核验代码和绑定，不能仅凭跳转标记通过。
 setup();UserVideoGuardResolver::Plan first;installed(first);UserVideoGuardResolver::Plan again;
 assert(resolve(again)&&again.installed&&again.page==first.page&&writes==0);cases++;
 for(unsigned kind=0;kind<7;kind++){
  setup();UserVideoGuardResolver::Plan good;installed(good);auto initial=blocks;
  switch(kind){
   case 0:word32(good.page+32,0x12345678);break;
   case 1:word64(good.page+Bindings[0].offset,0x12345678);break;
   case 2:word32(good.page+12,Features^1);break;
   case 3:word64(base+good.patches[0].offset+6,good.page+Entries[0].offset+1);break;
   case 4:put(base+good.patches[0].offset,good.original[0],good.patches[0].size);break;
   case 5:blocks[0x40001000]=blocks.at(good.page);word64(base+good.patches[0].offset+6,0x40001000+Entries[0].offset);break;
   case 6:word32(functionAddresses[0]+100,0x12345678);break;
  }
  initial=blocks;UserVideoGuardResolver::Plan bad;assert(!resolve(bad)&&blocks==initial&&writes==0);cases++;
 }
 // 原函数移动到跨页入口，绝对跳转不依赖守卫页与共享缓存的 rel32 距离。
 setup();auto old=functionAddresses[0];auto moved=old+120;
 put(old,ResolverFunctions[0].original,ResolverFunctions[0].size);word32(old,0);
 functionAddresses[0]=moved;put(moved,ResolverFunctions[0].original,ResolverFunctions[0].size);
 for(unsigned r=0;r<ResolverFunctions[0].originalCount;r++){auto ref=ResolverFunctions[0].originalRefs[r];rel(moved,ref.offset,destination(ref,ResolverFunctions,functions));}
 UserVideoGuardResolver::Plan cross;assert(resolve(cross)&&base+cross.patches[0].offset==moved);cases++;
 // 同型构造器的外部数据目标必须参与区分；相同目标的重复实现仍然拒绝。
 for(bool same:{false,true}){
  setup();constexpr unsigned ctor=3;const auto& fn=ResolverFunctions[ctor];auto clone=imageAddresses[fn.image]+0x68000;
  put(clone,fn.original,fn.size);
  for(unsigned r=0;r<fn.originalCount;r++){auto ref=fn.originalRefs[r];auto target=destination(ref,ResolverFunctions,functions);
   bool data=false;for(auto&t:SectionTargets)if(t.rva==ref.rva&&imagePaths[ref.image]==t.path)data=true;
   rel(clone,ref.offset,target+(!same&&data?64:0));}
  UserVideoGuardResolver::Plan candidate;assert(resolve(candidate)==!same);assert(writes==0);cases++;
 }
 setup();UserSharedCache::Input exhausted{reader,{}};exhausted.reads=32768;UserVideoGuardResolver::Plan failed;
 assert(!UserVideoGuardResolver::resolve(exhausted,base,major,failed)&&writes==0);cases++;
 UserSharedCache::Input unsupported{reader,{}};assert(!UserVideoGuardResolver::resolve(unsupported,base,23,failed));cases++;
 assert(!UserVideoGuardResolver::setPage(plan,0x40000001,major));assert(!UserVideoGuardResolver::setPage(plan,0x40000000,23));cases+=2;
 printf("PASS %u private guard resolver cases: moved sections, typed bindings, installed bytes, mixed pages and near matches\n",cases);
}
'''.replace('MAJOR',str(args.major))
    path=work/'test.cpp';path.write_text(source);exe=work/'test'
    flags=['-fsanitize=address,undefined','-fno-omit-frame-pointer'] if args.sanitize else []
    subprocess.run(['xcrun','clang++','-std=c++23','-O2',*flags,'-I'+str(work/'stubs'),'-I'+str(ROOT/'NootedRed'),'-I'+str(ROOT/'tools/fixtures'),str(path),'-o',str(exe)],check=True,timeout=60)
    subprocess.run([str(exe)],check=True,timeout=60)

if __name__=='__main__':main()
