"""编译实际内核桥接与解析器，用兼容但重排的缓存检验匹配、拒绝和跨页回滚。"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/user-surface-sync/dynamic'


def main():
    source = (ROOT / 'NootedRed/UserSurfaceSync.cpp').read_text()
    body = source.split('namespace {', 1)[1].split('} // namespace', 1)[0]
    init = source[source.index('void UserSurfaceSync::init('):]
    stubs = WORK / 'stubs/IOKit'
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    harness = r'''
#include <cassert>
#include <cstring>
#include <cstdint>
#include <cstdio>
#include <map>
#include <set>
#include <string>
#include <vector>
#include <array>
namespace Patch {} // 模拟 Lilu 的同名命名空间，防止独立测试遗漏集成歧义。
#include <UserSurfaceSyncData.hpp>
#include <UserSurfaceSyncResolver.hpp>
using namespace UserSurfaceSyncData;
using user_addr_t=uint64_t;using mach_vm_address_t=uint64_t;using mach_vm_size_t=uint64_t;
using vm_map_t=void*;using task_t=void*;using proc_t=void*;using natural_t=unsigned;
using mach_msg_type_number_t=unsigned;using vm_prot_t=int;using kern_return_t=int;
struct vm_region_submap_short_info_data_64_t {int protection=0,is_submap=0;};using vm_region_recurse_info_t=int*;
constexpr unsigned PAGE_SIZE=4096,VM_REGION_SUBMAP_SHORT_INFO_COUNT_64=12;
constexpr int FALSE=0,KERN_SUCCESS=0,KERN_INVALID_ARGUMENT=4,KERN_FAILURE=5;
constexpr int VM_PROT_NONE=0,VM_PROT_READ=1,VM_PROT_WRITE=2,VM_PROT_EXECUTE=4,VM_PROT_COPY=16;
#define SYSLOG(...) ((void)0)
template<typename T,size_t N>constexpr size_t arrsize(const T(&)[N]){return N;}
static std::map<std::string,unsigned> properties;
struct NRed{static NRed& singleton(){static NRed n;return n;}void setProp32(const char*k,unsigned v){properties[k]=v;}
 unsigned getDeviceID(){return device;}static unsigned device;};unsigned NRed::device=0x15e7;
struct Version{unsigned major(){return 25;}unsigned minor(){return 0;}bool majorMatches(unsigned v){return v==25;}};
constexpr unsigned MACOS_26=25;static Version currentKernelVersion(){return {};}
static bool checkKernelArgument(const char*s){return strcmp(s,"-NRedLegacyBlend")==0;}
struct KernelPatcher{static constexpr int KernelID=0;struct RouteRequest{template<typename T>RouteRequest(const char*,T,uint64_t&) {}};
 uint64_t solveSymbol(int,const char*){return 1;}void clearError(){}bool routeMultipleLong(int,RouteRequest*,int){return true;}};
namespace UserSurfaceSync{void init(KernelPatcher&);}
static task_t current_task(){return reinterpret_cast<void*>(1);}
static int proc_pid(proc_t){return 217;}static void proc_name(int,char*p,int n){snprintf(p,n,"WindowServer");}
static std::map<uint64_t,int> pageProtections;static std::set<unsigned> failedProtect;
static unsigned protects=0,writes=0,failWrite=0,regionFail=0;static bool noMap=false;
static kern_return_t vm_protect(vm_map_t,uint64_t a,unsigned,int,int flags){
 protects++;if(failedProtect.contains(protects))return 42;assert(pageProtections.contains(a));pageProtections[a]=flags&7;return 0;}
BODY
INIT
static constexpr uint64_t unslid=0x7ff900000000ULL,slide=0x1234000ULL;
static uint64_t base=unslid+slide;
static constexpr unsigned imageOffset=0x20000,textOffset=0x21000,textSize=0x20000,constantOffset=0x42000;
static constexpr unsigned initOffset=0x21f80,overrideOffset=0x23ff8,settingsOffset=0x26000,dumpOffset=0x27000;
static constexpr unsigned constructorOffset=0x28fe5,scratchOffset=0x2d000;
static std::vector<uint8_t> memory(0x60000),initial;
static unsigned reads=0;static uint64_t failedRead=0;static unsigned originalCalls=0;
static void put32(unsigned o,uint32_t v){memcpy(memory.data()+o,&v,4);}
static void put64(unsigned o,uint64_t v){memcpy(memory.data()+o,&v,8);}
static void copy(unsigned o,const void*p,unsigned n){assert(o+n<=memory.size());memcpy(memory.data()+o,p,n);}
static void rel(unsigned function,const RelativeField* fields,const uint64_t* targets){
 for(unsigned i=0;i<3;i++)assert(UserSurfaceSyncResolver::setRelative(memory.data()+function,fields[i].offset,
                                                                  base+function,targets[fields[i].target]));}
static int reader(uint64_t a,void*p,size_t n){
 reads++;if(a==0x1000){assert(n==8);memcpy(p,&base,8);return 0;}
 if(a==failedRead || a<base || a-base>memory.size() || n>memory.size()-(a-base))return 1;
 memcpy(p,memory.data()+a-base,n);return 0;}
static int writer(const void*p,uint64_t a,size_t n){
 writes++;for(auto page=a&~uint64_t(4095);page<=((a+n-1)&~uint64_t(4095));page+=4096)assert(pageProtections.at(page)==3);
 assert(a>=base && a-base+n<=memory.size());
 if(writes==failWrite){copy(unsigned(a-base),p,unsigned(n>2?2:n));return 1;}copy(unsigned(a-base),p,unsigned(n));return 0;}
static int original(proc_t,SharedRegionCheckArgs*,int*v){originalCalls++;*v=123;return 0;}
static vm_map_t map(task_t){return noMap?nullptr:reinterpret_cast<void*>(2);}
static kern_return_t query(vm_map_t,uint64_t*a,uint64_t*s,unsigned*,int*p,unsigned*c){
 if(regionFail)return 42;assert(*c==VM_REGION_SUBMAP_SHORT_INFO_COUNT_64);auto*i=reinterpret_cast<vm_region_submap_short_info_data_64_t*>(p);
 i->protection=pageProtections.at(*a);i->is_submap=0;*s=4096;return 0;}
static void reset(unsigned device=0x15e7){
 properties.clear();pageProtections.clear();failedProtect.clear();protects=writes=failWrite=regionFail=reads=originalCalls=0;
 failedRead=0;noMap=false;seenStages=appliedCount=errorCount=windowServerCount=weatherCount=0;
 NRed::device=device;KernelPatcher patcher;UserSurfaceSync::init(patcher);assert(properties.at("NRedImmediateSyncRevision")==4);
 assert(devicePatches);legacyBlend=srdShared=true;computeScratch=false;
 memset(memory.data(),0,memory.size());copy(0,CacheMagic,16);put32(16,0x300);put32(20,1);
 // 改变 UUID、缓存 slide、映像位置及三个补丁入口，代码仍为相同驱动实现。
 memset(memory.data()+88,0xa7,16);put64(224,unslid);put64(232,memory.size());put64(240,slide);
 put32(448,0x1000);put32(452,1);put64(0x300,unslid);put64(0x308,memory.size());put64(0x310,0);put32(0x31c,1);
 put64(0x1000,unslid+imageOffset);put32(0x1018,0x1800);copy(0x1800,DriverPath,sizeof(DriverPath));
 put32(imageOffset,0xfeedfacf);put32(imageOffset+4,0x1000007);put32(imageOffset+12,8);put32(imageOffset+16,1);put32(imageOffset+20,232);
 unsigned c=imageOffset+32;put32(c,0x19);put32(c+4,232);copy(c+8,"__TEXT",7);put64(c+24,unslid+imageOffset);
 put64(c+32,0x24000);put32(c+56,5);put32(c+60,5);put32(c+64,2);
 unsigned s=c+72;copy(s,"__text",7);copy(s+16,"__TEXT",7);put64(s+32,unslid+textOffset);put64(s+40,textSize);
 s+=80;copy(s,"__const",8);copy(s+16,"__TEXT",7);put64(s+32,unslid+constantOffset);put64(s+40,4096);
 copy(initOffset,Original0,sizeof(Original0));copy(overrideOffset,Original1,sizeof(Original1));
 copy(settingsOffset,NativeOverride,sizeof(NativeOverride));copy(dumpOffset,NativeDump,sizeof(NativeDump));
 copy(constructorOffset,BlendConstructor,sizeof(BlendConstructor));copy(scratchOffset,ScratchOriginal,sizeof(ScratchOriginal));
 put32(constantOffset+100,0x40400000);
 const uint64_t targets[]={base+constantOffset+100,base+settingsOffset,base+dumpOffset};rel(initOffset,OriginalInitLinks,targets);
 assert(UserSurfaceSyncResolver::setRelative(memory.data()+overrideOffset,6,base+overrideOffset,base+settingsOffset));
 const unsigned positions[]={initOffset,overrideOffset,constructorOffset+BlendGateInConstructor};
 const unsigned sizes[]={sizeof(Original0),sizeof(Original1),sizeof(BlendGateOriginal)};
 for(unsigned i=0;i<3;i++)for(uint64_t p=(base+positions[i])&~uint64_t(4095);p<=((base+positions[i]+sizes[i]-1)&~uint64_t(4095));p+=4096)pageProtections[p]=5;
 assert(pageProtections.size()==6);initial=memory;
 originalCheck=reinterpret_cast<uint64_t>(original);getTaskMap=map;queryRegion=query;readUser=reader;writeUser=writer;
}
static void call(){SharedRegionCheckArgs a{0x1000};int v=0;assert(wrappedSharedRegionCheck(reinterpret_cast<void*>(217),&a,&v)==0&&v==123);}
static void rx(){for(auto[a,p]:pageProtections)assert(p==5);}
static void untouched(){assert(memory==initial&&writes==0&&protects==0);}
int main(){
 unsigned cases=0;
 for(auto& device:DevicePatches)for(unsigned mode=0;mode<3;mode++){
  reset(device.deviceID);legacyBlend=mode==2;srdShared=mode!=0;
  UserSurfaceSyncResolver::Plan p;assert(UserSurfaceSyncResolver::resolve(reader,base,device,srdShared,legacyBlend,p));
  assert(p.patches[0].offset==initOffset&&p.patches[1].offset==overrideOffset&&p.patches[2].offset==constructorOffset+BlendGateInConstructor);
  assert(p.reads<32768);const uint64_t targets[]={base+constantOffset+100,base+settingsOffset,base+dumpOffset};
  auto* links=mode?SharedInitLinks:ImmediateInitLinks;
  for(unsigned j=0;j<3;j++){uint64_t target=0;assert(UserSurfaceSyncResolver::relativeTarget(base+initOffset,links[j].offset,p.patched[0],target));assert(target==targets[links[j].target]);}
  call();rx();assert(properties.at("NRedImmediateSyncDynamicApplied")==1&&properties.at("NRedImmediateSyncCacheUUID0")==0xa7a7a7a7);
  assert(writes==(mode==2?3U:2U)&&originalCalls==1);
  for(unsigned j=0;j<(mode==2?3U:2U);j++)assert(memcmp(memory.data()+p.patches[j].offset,p.patched[j],p.patches[j].size)==0);
  unsigned before=writes;call();assert(writes==before&&properties.at("NRedImmediateSyncD07")==1);cases++;
 }
 for(unsigned entry=1;entry<=3;entry++){reset();failWrite=entry;call();assert(memory==initial);rx();assert(!properties.contains("NRedImmediateSyncDynamicApplied"));cases++;}
 for(unsigned page=1;page<=6;page++){reset();failedProtect={page};call();assert(memory==initial&&writes==0);rx();cases++;}
 reset();failedProtect={7,8};call();assert(memory==initial);rx();assert(!properties.contains("NRedImmediateSyncDynamicApplied"));cases++;
 for(unsigned kind=0;kind<31;kind++){
  reset();
  switch(kind){
   case 0:memory[0]='?';break;
   case 1:put32(452,8193);break;
   case 2:put32(448,0xfffffff0);break;
   case 3:put32(0x1018,0xfffffff0);break;
   case 4:memory[0x1800]='?';break;
   case 5:put32(imageOffset,0);break;
   case 6:put32(imageOffset+16,129);break;
   case 7:put32(imageOffset+36,7);break;
   case 8:put64(imageOffset+32+72+40,UserSurfaceSyncResolver::MaxTextSize+1);break;
   case 9:memory[settingsOffset+30]^=1;break;
   case 10:memory[scratchOffset+100]^=1;break;
   case 11:memory[constructorOffset+100]^=1;break;
   case 12:put32(constantOffset+100,0);break;
   case 13:memory[dumpOffset]^=1;break;
   case 14:memory[initOffset+100]^=1;break;
   case 15:copy(0x35000,BlendConstructor,sizeof(BlendConstructor));break;
   case 16:copy(0x35000,memory.data()+initOffset,sizeof(Original0));rel(0x35000,OriginalInitLinks,(const uint64_t[]){base+constantOffset+100,base+settingsOffset,base+dumpOffset});break;
   case 17:failedRead=base+0x1000;break;
   case 18:put64(232,UserSurfaceSyncResolver::UserLimit);break;
   case 19:copy(0x35000,NativeOverride,sizeof(NativeOverride));break;
   case 20:assert(UserSurfaceSyncResolver::setRelative(memory.data()+overrideOffset,6,base+overrideOffset,base+dumpOffset));break;
   case 21:for(auto link:OriginalInitLinks)if(link.target==1)assert(UserSurfaceSyncResolver::setRelative(memory.data()+initOffset,link.offset,base+initOffset,base+dumpOffset));break;
   case 22:put32(0x40000,0x40400000);for(auto link:OriginalInitLinks)if(link.target==0)assert(UserSurfaceSyncResolver::setRelative(memory.data()+initOffset,link.offset,base+initOffset,base+0x40000));break;
   case 23:copy(constantOffset+1000,NativeDump,sizeof(NativeDump));for(auto link:OriginalInitLinks)if(link.target==2)assert(UserSurfaceSyncResolver::setRelative(memory.data()+initOffset,link.offset,base+initOffset,base+constantOffset+1000));break;
   case 24:copy(scratchOffset,ScratchPatched,sizeof(ScratchPatched));break;
   case 25:memory[constructorOffset+BlendGateInConstructor]=memory[constructorOffset+BlendGateInConstructor+1]=1;break;
   case 26:{copy(initOffset,devicePatches->shared[0].patched,sizeof(Original0));const uint64_t targets[]={base+constantOffset+100,base+settingsOffset,base+dumpOffset};rel(initOffset,SharedInitLinks,targets);}break;
   case 27:put32(452,2);copy(0x1020,memory.data()+0x1000,32);break;
   case 28:put32(imageOffset+32+64,65);break;
   case 29:failedRead=base+settingsOffset;break;
   case 30:for(unsigned p=textOffset;p+32<textOffset+textSize;p+=32)copy(p,NativeOverride,32);break;
  }
  initial=memory;call();untouched();assert(!properties.contains("NRedImmediateSyncDynamicApplied"));cases++;
 }
 reset();regionFail=1;call();untouched();cases++;
 reset();noMap=true;call();untouched();cases++;
 reset();computeScratch=true;call();untouched();assert(properties.at("NRedImmediateSyncDynamicRejected")==1);cases++;
 // 混合策略必须拒绝；不能把只有 SRD 的模式报告为已经应用 LegacyBlend。
 reset();call();assert(properties.at("NRedImmediateSyncDynamicApplied")==1);legacyBlend=false;protects=writes=0;initial=memory;
 call();untouched();assert(properties.at("NRedImmediateSyncD06")==1);cases++;
 uint8_t field[4]{};uint64_t t;
 assert(!UserSurfaceSyncResolver::setRelative(field,0,UINT64_MAX,0));
 assert(!UserSurfaceSyncResolver::relativeTarget(UINT64_MAX,0,field,t));
 assert(!UserSurfaceSyncResolver::setRelative(field,0,0x100000000ULL,0));
 assert(UserSurfaceSyncResolver::setRelative(field,0,0x100000000ULL,0x80000004ULL));
 assert(UserSurfaceSyncResolver::relativeTarget(0x100000000ULL,0,field,t)&&t==0x80000004ULL);
 printf("PASS %u actual bridge cases: all Vega IDs; unknown UUID and relocated code; full function/target gates; six-page rollback; bounded reads\n",cases);
}
'''.replace('BODY', body).replace('INIT', init)
    # 不依赖编译器的 C99 compound literal 扩展。
    harness = harness.replace('rel(0x35000,OriginalInitLinks,(const uint64_t[]){base+constantOffset+100,base+settingsOffset,base+dumpOffset});',
                              '{const uint64_t targets[]={base+constantOffset+100,base+settingsOffset,base+dumpOffset};rel(0x35000,OriginalInitLinks,targets);}')
    file = WORK / 'test-dynamic.cpp'
    file.write_text(harness)
    exe = WORK / 'test-dynamic'
    flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if '--sanitize' in sys.argv else []
    subprocess.run(['clang++', '-std=c++23', '-O2', '-Wall', '-Wextra', '-Wshadow', '-Werror', *flags,
                    '-I', str(WORK / 'stubs'), '-I', str(ROOT / 'NootedRed'), str(file), '-o', str(exe)],
                   check=True, timeout=60)
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
