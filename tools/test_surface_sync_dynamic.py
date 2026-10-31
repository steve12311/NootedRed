"""编译实际内核桥接与解析器，用兼容但重排的缓存检验匹配、拒绝和跨页回滚。"""
from pathlib import Path
from user_patch_memory_harness import with_memory_helpers
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/user-surface-sync/dynamic'


def main():
    source = (ROOT / 'NootedRed/UserSurfaceSync.cpp').read_text()
    body = source.split('namespace {', 1)[1].split('} // namespace', 1)[0]
    body = with_memory_helpers(body)
    init = source[source.index('void UserSurfaceSync::init('):]
    stubs = WORK / 'stubs/IOKit'
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt16=uint16_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
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
#include <UserLegacyData.hpp>
#include <UserSubmitGuardData.hpp>
#include <coordinator-inactive.hpp>
#include <VPF/DisplayRuntime.hpp>
#include <VPF/ExecutableTrampolineResource.hpp>
#include <VPF/MetalTransactionExecutor.hpp>
#include <VPF/TransactionExecutor.hpp>
#include <VPF/VerifiedPatchPlan.hpp>
namespace Buffer {
template<class T>T* create(size_t count){return new T[count];}
template<class T>void deleter(T* pointer){delete[] pointer;}
}
using namespace UserSurfaceSyncData;
using user_addr_t=uint64_t;using mach_vm_address_t=uint64_t;using mach_vm_size_t=uint64_t;
using vm_map_t=void*;using task_t=void*;using proc_t=void*;using natural_t=unsigned;
using mach_msg_type_number_t=unsigned;using vm_prot_t=int;using kern_return_t=int;
struct vm_region_submap_info_data_64_t {int protection=0,max_protection=0,is_submap=0;unsigned object_id=0;
 unsigned char share_mode=0;unsigned short shadow_depth=0;};
using vm_region_submap_short_info_data_64_t=vm_region_submap_info_data_64_t;using vm_region_recurse_info_t=int*;
constexpr unsigned PAGE_SIZE=4096,VM_REGION_SUBMAP_SHORT_INFO_COUNT_64=12;
constexpr int TRUE=1,FALSE=0,KERN_SUCCESS=0,KERN_INVALID_ADDRESS=1,KERN_PROTECTION_FAILURE=2,KERN_RESOURCE_SHORTAGE=3,KERN_INVALID_ARGUMENT=4,KERN_FAILURE=5;
constexpr int VM_FLAGS_ANYWHERE=1;
using vm_address_t=uint64_t;
constexpr int VM_PROT_NONE=0,VM_PROT_READ=1,VM_PROT_WRITE=2,VM_PROT_EXECUTE=4,VM_PROT_COPY=16;
template<typename... Args>static void testLog(Args...){}
#define SYSLOG(...) testLog(__VA_ARGS__)
#define IONew(T,N) new T[N]
#define IODelete(P,T,N) delete[] P
template<typename T,size_t N>constexpr size_t arrsize(const T(&)[N]){return N;}
static std::map<std::string,unsigned> properties;
struct NRed{static NRed& singleton(){static NRed n;return n;}void setProp32(const char*k,unsigned v){properties[k]=v;}
 unsigned getDeviceID(){return device;}static unsigned device;};unsigned NRed::device=0x15e7;
struct Version{unsigned major(){return 25;}unsigned minor(){return 0;}bool majorMatches(unsigned v){return v==25;}};
constexpr unsigned MACOS_26=25;static Version currentKernelVersion(){return {};}
namespace UserPatchCoordinator {inline UInt32 requestedDisplayGroups(){return VPF::MetalDisplayGroups;}}
static bool noFvrc=false;
static bool checkKernelArgument(const char*s){
 if(strcmp(s,"-NRedNoFVRC")==0)return noFvrc;
 return strcmp(s,"-NRedDisplayFix")==0;}
struct KernelPatcher{static constexpr int KernelID=0;struct RouteRequest{template<typename T>RouteRequest(const char*,T,uint64_t&) {}};
 uint64_t solveSymbol(int,const char*){return 1;}void clearError(){}bool routeMultipleLong(int,RouteRequest*,int){return true;}};
namespace UserSurfaceSync{void init(KernelPatcher&);}
static task_t current_task(){return reinterpret_cast<void*>(1);}
static int proc_pid(proc_t){return 217;}static void proc_name(int,char*p,int n){snprintf(p,n,"WindowServer");}
static std::map<uint64_t,int> pageProtections;static std::set<unsigned> failedProtect;
static unsigned protects=0,writes=0,failWrite=0,regionFail=0;static bool noMap=false;
static kern_return_t vm_allocate(vm_map_t,vm_address_t* page,unsigned,int){*page=0;return KERN_FAILURE;}
static kern_return_t vm_deallocate(vm_map_t,vm_address_t,unsigned){return KERN_SUCCESS;}
static kern_return_t vm_protect(vm_map_t,uint64_t a,unsigned,int setMax,int flags){
 protects++;if(failedProtect.contains(protects))return 42;assert(pageProtections.contains(a));
 if(setMax)pageProtections[a]&=flags&7;else pageProtections[a]=flags&7;return 0;}
BODY
INIT
static constexpr uint64_t unslid=0x7ff900000000ULL;static uint64_t slide=0x1234000ULL;
static uint64_t base=unslid+slide;
static constexpr unsigned imageOffset=0x20000,textOffset=0x21000,textSize=0x20000,constantOffset=0x42000;
static constexpr unsigned initOffset=0x21f80,overrideOffset=0x23ff8,settingsOffset=0x26000,dumpOffset=0x27000;
static constexpr unsigned constructorOffset=0x28fe5,scratchOffset=0x2d000,submitOffset=0x30000;
static constexpr uint64_t guardAddress=0x600000;
static std::vector<uint8_t> memory(0x60000),initial;
static std::array<uint8_t,4096> guardPage;
static unsigned reads=0;static uint64_t failedRead=0;static unsigned originalCalls=0;
static void put32(unsigned o,uint32_t v){memcpy(memory.data()+o,&v,4);}
static void put64(unsigned o,uint64_t v){memcpy(memory.data()+o,&v,8);}
static void copy(unsigned o,const void*p,unsigned n){assert(o+n<=memory.size());memcpy(memory.data()+o,p,n);}
static void rel(unsigned function,const RelativeField* fields,const uint64_t* targets){
 for(unsigned i=0;i<3;i++)assert(UserSurfaceSyncResolver::setRelative(memory.data()+function,fields[i].offset,
                                                                  base+function,targets[fields[i].target]));}
static int reader(uint64_t a,void*p,size_t n){
 reads++;if(a==0x1000){assert(n==8);memcpy(p,&base,8);return 0;}
 if(a==guardAddress){assert(n<=guardPage.size());memcpy(p,guardPage.data(),n);return 0;}
 if(a==failedRead || a<base || a-base>memory.size() || n>memory.size()-(a-base))return 1;
 memcpy(p,memory.data()+a-base,n);return 0;}
static int writer(const void*p,uint64_t a,size_t n){
 writes++;for(auto page=a&~uint64_t(4095);page<=((a+n-1)&~uint64_t(4095));page+=4096)assert(pageProtections.at(page)==3);
 assert(a>=base && a-base+n<=memory.size());
 if(writes==failWrite){copy(unsigned(a-base),p,unsigned(n>2?2:n));return 1;}copy(unsigned(a-base),p,unsigned(n));return 0;}
static int original(proc_t,SharedRegionCheckArgs*,int*v){originalCalls++;*v=123;return 0;}
static vm_map_t map(task_t){return noMap?nullptr:reinterpret_cast<void*>(2);}
static unsigned mapShare=5;
static kern_return_t query(vm_map_t,uint64_t*a,uint64_t*s,unsigned*,int*p,unsigned*c){
 if(regionFail)return 42;assert(*c==VM_REGION_SUBMAP_SHORT_INFO_COUNT_64);auto*i=reinterpret_cast<vm_region_submap_short_info_data_64_t*>(p);
 i->protection=pageProtections.contains(*a)?pageProtections.at(*a):1;i->is_submap=0;*s=4096;
 i->object_id=0x1234;i->share_mode=uint8_t(mapShare);i->shadow_depth=0;return 0;}
static bool probe(void*,uint64_t address,uint32_t depth,VPF::MappingInfo&info){
 info={address,4096,depth,0x1234,uint8_t(mapShare),0,false,false};return true;}
static void reset(unsigned device=0x15e7){
 base=unslid+slide;
 properties.clear();pageProtections.clear();failedProtect.clear();protects=writes=failWrite=regionFail=reads=originalCalls=0;
 failedRead=0;noMap=false;published={};seenStages=appliedCount=errorCount=windowServerCount=weatherCount=0;
 NRed::device=device;KernelPatcher patcher;UserSurfaceSync::init(patcher);assert(properties.at("NRedImmediateSyncRevision")==4);
 assert(devicePatches);legacyBlend=srdShared=true;computeScratch=false;submitGuard=true;
 memset(memory.data(),0,memory.size());copy(0,CacheMagic,16);put32(16,0x300);put32(20,1);
 // 改变 UUID、缓存 slide、映像位置及三个补丁入口，代码仍为相同驱动实现。
 memset(memory.data()+88,0xa7,16);put64(224,unslid);put64(232,0x40000000ULL);put64(240,slide);
 put32(448,0x1000);put32(452,1);put64(0x300,unslid);put64(0x308,memory.size());put64(0x310,0);put32(0x31c,1);
 put64(0x1000,unslid+imageOffset);put32(0x1018,0x1800);copy(0x1800,DriverPath,sizeof(DriverPath));
 put32(imageOffset,0xfeedfacf);put32(imageOffset+4,0x1000007);put32(imageOffset+12,8);put32(imageOffset+16,3);put32(imageOffset+20,1176);
 unsigned c=imageOffset+32;put32(c,0x19);put32(c+4,232);copy(c+8,"__TEXT",7);put64(c+24,unslid+imageOffset);
 put64(c+32,0x24000);put32(c+56,5);put32(c+60,5);put32(c+64,2);
 unsigned s=c+72;copy(s,"__text",7);copy(s+16,"__TEXT",7);put64(s+32,unslid+textOffset);put64(s+40,textSize);
 s+=80;copy(s,"__const",8);copy(s+16,"__TEXT",7);put64(s+32,unslid+constantOffset);put64(s+40,4096);
 // 提交方法的两个 objc 引用需要真实的节目标；第三个引用落在 __DATA 段内。
 unsigned d=c+232;put32(d,0x19);put32(d+4,872);copy(d+8,"__DATA_CONST",13);
 put64(d+24,unslid+0x33000000ULL);put64(d+32,0x100000);put32(d+56,3);put32(d+60,3);put32(d+64,10);
 for(unsigned j=0;j<10;j++){unsigned sec=d+72+j*80;
  const char* name=j==7?"__objc_selrefs":(j==9?"__objc_superrefs":"__pad");
  copy(sec,name,unsigned(strlen(name)+1));copy(sec+16,"__DATA_CONST",13);
  put64(sec+32,unslid+0x33000000ULL+(j==9?0x1000:0));put64(sec+40,0x1000);}
 unsigned e=d+872;put32(e,0x19);put32(e+4,72);copy(e+8,"__DATA",7);
 put64(e+24,unslid+0x360000ULL);put64(e+32,0x100000);put32(e+56,3);put32(e+60,3);put32(e+64,0);
 copy(initOffset,Original0,sizeof(Original0));copy(overrideOffset,Original1,sizeof(Original1));
 copy(settingsOffset,NativeOverride,sizeof(NativeOverride));copy(dumpOffset,NativeDump,sizeof(NativeDump));
 copy(constructorOffset,BlendConstructor,sizeof(BlendConstructor));copy(scratchOffset,ScratchOriginal,sizeof(ScratchOriginal));
 put32(constantOffset+100,0x40400000);
 const uint64_t targets[]={base+constantOffset+100,base+settingsOffset,base+dumpOffset};rel(initOffset,OriginalInitLinks,targets);
 assert(UserSurfaceSyncResolver::setRelative(memory.data()+overrideOffset,6,base+overrideOffset,base+settingsOffset));
 copy(submitOffset,UserSurfaceConstructorData::Submit::ResolverOriginal0,
      sizeof(UserSurfaceConstructorData::Submit::ResolverOriginal0));
 const auto& submitRefs=UserSurfaceConstructorData::Submit::ResolverOriginalRefs0;
 const uint64_t submitTargets[]={base+0x33001090ULL,base+0x33000A98ULL,base+0x389828ULL};
 for(unsigned i=0;i<3;i++)assert(UserSurfaceSyncResolver::setRelative(memory.data()+submitOffset,
  submitRefs[i].offset,base+submitOffset,submitTargets[i]));
 uint8_t payload[sizeof(UserSubmitGuardData::Payload)];memcpy(payload,UserSubmitGuardData::Payload,sizeof(payload));
 const uint64_t continuation=base+submitOffset+UserSubmitGuardData::MethodPatchSize;
 memcpy(payload+UserSubmitGuardData::PayloadJumpOffset,&continuation,sizeof(continuation));
 guardPage.fill(0);memcpy(guardPage.data(),payload,sizeof(payload));
 uint8_t entry[15]={0xff,0x25,0,0,0,0};memcpy(entry+6,&guardAddress,sizeof(guardAddress));entry[14]=0x90;
 copy(submitOffset,entry,sizeof(entry));
 const unsigned positions[]={initOffset,overrideOffset,constructorOffset+BlendGateInConstructor};
 const unsigned sizes[]={sizeof(Original0),sizeof(Original1),sizeof(BlendGateOriginal)};
 for(unsigned i=0;i<3;i++)for(uint64_t p=(base+positions[i])&~uint64_t(4095);p<=((base+positions[i]+sizes[i]-1)&~uint64_t(4095));p+=4096)pageProtections[p]=5;
 pageProtections[guardAddress]=5;
 assert(pageProtections.size()==7);initial=memory;
 originalCheck=reinterpret_cast<uint64_t>(original);getTaskMap=map;queryRegion=query;readUser=reader;writeUser=writer;
}
static void call(){SharedRegionCheckArgs a{0x1000};int v=0;assert(wrappedSharedRegionCheck(reinterpret_cast<void*>(217),&a,&v)==0&&v==123);}
static void rx(){for(auto[a,p]:pageProtections)assert(p==5);}
static void untouched(){assert(memory==initial&&writes==0&&protects==0);}
int main(){
 unsigned cases=0;
 noFvrc=true;reset();assert(properties.at("NRedFVRCEnabled")==0);
 noFvrc=false;reset();assert(properties.at("NRedFVRCEnabled")==1&&properties.at("NRedFVRCCacheReady")==1);
 for(auto& device:DevicePatches)for(unsigned mode=0;mode<3;mode++){
  reset(device.deviceID);legacyBlend=mode==2;srdShared=mode!=0;
  UserSurfaceSyncResolver::Plan p;assert(UserSurfaceSyncResolver::resolve(reader,base,device,srdShared,legacyBlend,p));
  assert(p.patches[0].offset==initOffset&&p.patches[1].offset==overrideOffset&&p.patches[2].offset==constructorOffset+BlendGateInConstructor);
  assert(p.reads<32768);const uint64_t targets[]={base+constantOffset+100,base+settingsOffset,base+dumpOffset};
  auto* links=mode?SharedInitLinks:ImmediateInitLinks;
  for(unsigned j=0;j<3;j++){uint64_t target=0;assert(UserSurfaceSyncResolver::relativeTarget(base+initOffset,links[j].offset,p.patched[0],target));assert(target==targets[links[j].target]);}
  call();rx();assert(properties.at("NRedImmediateSyncDynamicApplied")==1&&properties.at("NRedImmediateSyncCacheUUID0")==0xa7a7a7a7);
  assert(properties.at("NRedDisplayVPFPlanVerified")==1&&properties.at("NRedDisplayVPFPlanSites")==(mode==2?3:2));
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
 // FVRC：同布局跨 slide 复用；私有映射回退完整扫描。
 VPF::ResolutionDiagnostics::setEnabled(true);VPF::resolutionCache.init();
 slide=0x1234000;reset();VPF::ResolutionDiagnostics::setEnabled(true);
 UserSurfaceSyncResolver::Plan fvrcFirst{};UserSharedCache::Input fvrcFirstInput{reader,{}};
 VPF::FastResolution fastFirst;fastFirst.begin(&memory[88],24,VPF::resolutionProfile(NRed::device,0),0,probe,nullptr);
 assert(UserSurfaceSyncResolver::resolve(fvrcFirstInput,base,*devicePatches,true,true,fvrcFirst,false,&fastFirst));
 assert(!fastFirst.hot);
 slide+=0x40000000ULL;reset();VPF::ResolutionDiagnostics::setEnabled(true);
 UserSurfaceSyncResolver::Plan fvrcSecond{};UserSharedCache::Input fvrcSecondInput{reader,{}};
 VPF::FastResolution fastSecond;fastSecond.begin(&memory[88],24,VPF::resolutionProfile(NRed::device,0),0,probe,nullptr);
 assert(UserSurfaceSyncResolver::resolve(fvrcSecondInput,base,*devicePatches,true,true,fvrcSecond,false,&fastSecond));
 assert(fastSecond.hot);
 for(unsigned i=0;i<3;i++)assert(fvrcFirst.patches[i].offset==fvrcSecond.patches[i].offset);
 mapShare=2;slide+=0x40000000ULL;reset();VPF::ResolutionDiagnostics::setEnabled(true);
 UserSurfaceSyncResolver::Plan fvrcDirty{};UserSharedCache::Input fvrcDirtyInput{reader,{}};
 VPF::FastResolution fastDirty;fastDirty.begin(&memory[88],24,VPF::resolutionProfile(NRed::device,0),0,probe,nullptr);
 assert(UserSurfaceSyncResolver::resolve(fvrcDirtyInput,base,*devicePatches,true,true,fvrcDirty,false,&fastDirty));
 assert(!fastDirty.hot);
 for(unsigned i=0;i<3;i++)assert(fvrcFirst.patches[i].offset==fvrcDirty.patches[i].offset);
 mapShare=5;cases++;
 printf("PASS %u actual bridge cases: all Vega IDs; unknown UUID and relocated code; full function/target gates; six-page rollback; bounded reads; FVRC cross-slide reuse\n",cases);
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
                    '-I', str(WORK / 'stubs'), '-I', str(ROOT / 'NootedRed'), '-I', str(ROOT / 'tools/fixtures'),
                    str(file), '-o', str(exe)], check=True, timeout=60)
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
