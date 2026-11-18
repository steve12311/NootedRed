"""编译真实视频桥接，验证未知 UUID、重排、绑定与跨页事务。"""
from pathlib import Path
from user_patch_memory_harness import with_memory_helpers
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SONOMA = '--sonoma' in sys.argv
LEGACY = SONOMA or '--sequoia' in sys.argv
WORK = ROOT / ('build/dynamic-cache/sonoma-video' if SONOMA else 'build/dynamic-cache/sequoia-video' if '--sequoia' in sys.argv else 'build/dynamic-cache/video')


def main():
    stub = WORK / 'stubs/IOKit'
    stub.mkdir(parents=True, exist_ok=True)
    (stub / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    (stub / 'IOUserClient.h').write_text((ROOT / 'tools/fixtures/fairplay-entitlement.hpp.in').read_text())
    boolean = WORK / 'stubs/libkern/c++'
    boolean.mkdir(parents=True, exist_ok=True)
    (boolean / 'OSBoolean.h').write_text('#pragma once\n#include <IOKit/IOUserClient.h>\n')
    (stub / 'IOLocks.h').write_text('#pragma once\nstruct IOLock;\nstatic void IOLockLock(IOLock*);static void IOLockUnlock(IOLock*);\n')
    (stub / 'IOLib.h').write_text('#pragma once\n#include <IOKit/IOLocks.h>\nstatic IOLock* IOLockAlloc();\n')
    (WORK / 'stubs/NRed.hpp').write_text('#pragma once\n')
    clock = WORK / 'stubs/kern'
    clock.mkdir(exist_ok=True)
    (clock / 'clock.h').write_text('#pragma once\ninline UInt64 mach_absolute_time(){return 1;}\ninline void absolutetime_to_nanoseconds(UInt64 a,UInt64* n){*n=a;}\n')
    util = WORK / 'stubs/Headers'
    util.mkdir(parents=True, exist_ok=True)
    (util / 'kern_util.hpp').write_text('#pragma once\n#include <IOKit/IOTypes.h>\n')
    source = (ROOT / 'NootedRed/UserVideoDecode.cpp').read_text()
    if '--baseline' in sys.argv:
        source = subprocess.check_output(['git','show','6c7b0804b5b115052fab5914eecfad7090b53eb1:NootedRed/UserVideoDecode.cpp'],cwd=ROOT,text=True)
    body = re.split(r'namespace\s*\{', source, maxsplit=1)[1].rsplit('}    // namespace', 1)[0]
    body = with_memory_helpers(body)
    template = (ROOT / 'tools/fixtures/vcn-user-bridge.cpp.in').read_text()
    preamble = template
    begin = preamble.index('namespace UserPatchCoordinator')
    end = preamble.index('}    // namespace UserPatchCoordinator', begin) + len('}    // namespace UserPatchCoordinator')
    preamble = preamble[:begin] + r'''
#include <sys/errno.h>
namespace UserPatchCoordinator {
constexpr unsigned HeaderSize=104;
enum class Outcome {Pending,Idle,Patched};
struct Participant {Outcome outcome=Outcome::Pending;unsigned proofSites=0;};
struct Transaction {Participant video,vp9;bool sharedHeader(UInt64&,UInt8*){return false;}};
using PatchCallback=int(*)(void*,void*,int,Transaction&);
inline bool deferModulePatches(){return false;}
inline void clearVideo(){} inline void registerVideo(PatchCallback){}
inline void forgetMap(void*){} inline bool processAllowed(void*){return true;}
template<class T>void settlePatched(Participant& p,const T& plan){p.outcome=Outcome::Patched;p.proofSites=plan.siteCount();}
}
''' + preamble[end:]
    preamble = preamble.replace('assert(strcmp(s,"-NRedVideoFix")==0||strcmp(s,"-NRedNoFVRC")==0);',
                                'if(strcmp(s,"-NRedVideoFix")&&strcmp(s,"-NRedNoFVRC"))return false;')
    preamble = preamble.replace('bool allocated(){return active;}', 'static bool vp9Ready=false;bool allocated(){return active;}void setVP9Ready(bool v){vp9Ready=v;}')
    preamble = preamble.replace('#include "UserVideoDecodeData.hpp"', '#include <vector>\n#include "UserVideoDecodeData.hpp"\n#include "UserVideoDecodeResolver.hpp"')
    preamble += '#include "user-cache-dynamic.hpp"\n'
    preamble = preamble.replace('#define IONew(T,N) (new T[N])\n#define IODelete(P,T,N) delete[] P', r'''
#include <cstdlib>
#include <type_traits>
struct TransactionState;
static bool failTransaction=false,failDynamic=false;
static unsigned liveTransactions=0,liveDynamicPlans=0;
template<class T>T* trackedNew(size_t count){
 if constexpr(std::is_same_v<T,TransactionState>){if(failTransaction)return nullptr;}
 if constexpr(std::is_same_v<T,UserVideoDecodeResolver::Plan>){if(failDynamic)return nullptr;}
 auto* p=static_cast<T*>(malloc(sizeof(T)*count));if(!p)return nullptr;
 memset(p,0xa5,sizeof(T)*count);
 if constexpr(std::is_same_v<T,TransactionState>)liveTransactions++;
 if constexpr(std::is_same_v<T,UserVideoDecodeResolver::Plan>)liveDynamicPlans++;
 return p;
}
template<class T>void trackedDelete(T* p){
 if constexpr(std::is_same_v<T,TransactionState>){assert(liveTransactions>0);liveTransactions--;}
 if constexpr(std::is_same_v<T,UserVideoDecodeResolver::Plan>){assert(liveDynamicPlans>0);liveDynamicPlans--;}
 free(p);
}
#define IONew(T,N) trackedNew<T>(N)
#define IODelete(P,T,N) trackedDelete(P)
''')

    harness = preamble + r'''
// 当前源码使用 IOLock 与 map 门；测试夹具提供单进程替身，不涉及真实锁语义。
#include <mutex>
#include <condition_variable>
struct IOLock { std::mutex mutex; };
static IOLock* IOLockAlloc(){return new IOLock;}
static void IOLockFree(IOLock* p){delete p;}
static void IOLockLock(IOLock* p){p->mutex.lock();}
static void IOLockUnlock(IOLock* p){p->mutex.unlock();}
constexpr int THREAD_UNINT=0;
static std::condition_variable_any gateChanged;
static int IOLockSleep(IOLock* p,void*,int){std::unique_lock<std::mutex> held(p->mutex,std::adopt_lock);gateChanged.wait(held);held.release();return 0;}
static void IOLockWakeup(IOLock*,void*,bool){gateChanged.notify_all();}
#include <UserPatchMapGate.hpp>
static bool drmClient=false;
static const char* clientName="Music";
static std::set<uint64_t> drmTouchedPages;
static std::map<uint64_t,int> maxima;
static kern_return_t vm_protect(vm_map_t,uint64_t a,unsigned,int setMax,int flags){
 protects++;if(protects==failedProtect || failedProtectCalls.contains(protects))return 42;assert(protections.contains(a));
 drmTouchedPages.insert(a);
 int value=flags&7;
 if(setMax){maxima[a]=value;protections[a]&=value;}else{protections[a]=value;maxima[a]|=value;}
 return 0;}
#include <UserVP9DecodeResolver.hpp>
#include <UserRejectedCaches.hpp>
#include <UserPhaseTiming.hpp>
namespace UserPhaseTiming {
UInt8& enabledFlag(){static UInt8 f=0;return f;}
Slot* slots(){static Slot v[PhaseCount]{};return v;}
UInt32& publishTicket(){static UInt32 n=0;return n;}
}
BODY
static int original(proc_t,SharedRegionCheckArgs*,int* value){assert(liveTransactions==0&&liveDynamicPlans==0);*value=123;return 0;}
static int proc_pid(proc_t){return 217;}static void proc_name(int,char* name,int size){snprintf(name,size,"%s",drmClient?clientName:"VTDecoderXPC");}
static vm_map_t map(task_t){return reinterpret_cast<void*>(2);}
static std::map<uint64_t,std::vector<uint8_t>> cleanBlocks;
static bool cleanPage(uint64_t a){
 const uint64_t page=a&~uint64_t(4095);
 auto bi=blocks.upper_bound(page);if(bi==blocks.begin())return false;--bi;
 auto ci=cleanBlocks.upper_bound(page);if(ci==cleanBlocks.begin())return false;--ci;
 if(bi->first!=ci->first)return false;
 const uint64_t off=page-bi->first;
 return off+4096<=bi->second.size()&&off+4096<=ci->second.size()
        &&memcmp(bi->second.data()+off,ci->second.data()+off,4096)==0;
}
static kern_return_t query(vm_map_t,uint64_t* a,uint64_t* size,unsigned*,int* p,unsigned* count){
 assert(*count==VM_REGION_SUBMAP_SHORT_INFO_COUNT_64);auto*i=reinterpret_cast<vm_region_submap_short_info_data_64_t*>(p);
 i->protection=protections.contains(*a)?protections.at(*a):1;i->max_protection=maxima.contains(*a)?maxima.at(*a):7;
 i->is_submap=0;*size=4096;
 i->object_id=0x1234;i->share_mode=cleanPage(*a)?5:2;i->shadow_depth=0;return 0;}
static void setup(unsigned id){
 using namespace UserVideoDecodeData;cache(ResolverImages,ResolverFunctions,true);cleanBlocks=blocks;
 NRed::device=id;devicePatches=findDevice(id);assert(devicePatches);
 VPF::ResolutionDiagnostics::setEnabled(true);VPF::resolutionCache.init();
 drmClient=false;clientEntitlement=nullptr;drmTouchedPages.clear();maxima.clear();
 published={};seenStages=appliedCount=errorCount=0;originalCheck=reinterpret_cast<uint64_t>(original);readUser=reader;writeUser=writer;getTaskMap=map;queryRegion=query;
}
static void call(){SharedRegionCheckArgs args{0x1000};int value=0;assert(wrappedSharedRegionCheck(drmClient?reinterpret_cast<void*>(217):nullptr,&args,&value)==0&&value==123);assert(liveTransactions==0&&liveDynamicPlans==0);}
int main(){
#ifndef BASELINE
 auto checkRDD=[]<typename Data>(unsigned major){
  const auto prepare=[&]{
   cache(Data::Images,Data::Functions,true,Data::Targets,std::size(Data::Targets));
   readUser=reader;
  };
  prepare();
  UserVideoDecodeResolver::RDDPlan candidate;
  uint8_t scan[UserSharedCache::Input::ScanSize];
  UserSharedCache::Input input{reader,{}};
  input.scanBuffer=scan;
  const auto resolve=[&](unsigned version){
   input.reads=input.bytes=input.candidates=0;input.failed=false;
   return UserVideoDecodeResolver::resolveRDD(input,base,candidate,version);
  };
  assert(!resolve(22)&&!resolve(26));
  assert(resolve(major));
  VPF::FastResolution fast{};
  VPF::VerifiedPatchPlan install;
  install.reset(base,input.cache);
  assert(appendVTRDDCandidates(install,input,base,major,candidate,fast));
  assert(install.siteCount()==std::size(Data::Sites));
  assert(install.seal(VPF::PlanState::AllOriginal));
  for(unsigned i=0;i<std::size(Data::Sites);++i){
   const auto& patch=candidate.patches[i];
   const auto& loc=Data::Locations[i];
   assert(patch.offset==functionAddresses[loc.function]+loc.offset-base);
   assert(patch.offset!=Data::Sites[i].offset);
   std::vector<uint8_t> actual(patch.size);
   assert(reader(base+patch.offset,actual.data(),actual.size())==0);
   assert(memcmp(actual.data(),patch.original,actual.size())==0);
   put(base+patch.offset,patch.patched,patch.size);
  }
  assert(resolve(major));
  assert(candidate.count==std::size(Data::Sites));
  for(unsigned i=0;i<std::size(Data::Functions);++i)assert(candidate.locations[i].address&&candidate.locations[i].patched);

  prepare();
  const auto& last=Data::Locations[std::size(Data::Sites)-1];
  uint8_t invalid=0xCC;
  put(functionAddresses[last.function]+last.offset,&invalid,1);
  assert(!resolve(major));
  install.reset(base,input.cache);
  assert(appendVTRDDCandidates(install,input,base,major,candidate,fast));
  assert(install.siteCount()==0);

  prepare();
  const auto& ref=Data::Functions[0].originalRefs[0];
  rel(functionAddresses[0],ref.offset,destination(ref,Data::Functions,std::size(Data::Functions))+8);
  assert(!resolve(major));
 };
 checkRDD.template operator()<UserVTRDDCandidateData::Sonoma::Profile>(23);
 checkRDD.template operator()<UserPatchProfiles::Sequoia::VTRDD>(24);
 checkRDD.template operator()<UserPatchProfiles::Tahoe::VTRDD>(25);
#endif
 // 执行真实初始化入口：三系统注册相同生命周期和共享缓存 Hook。
 for(unsigned major:{23U,24U,25U}){
  Version::majorValue=major;NRed::device=0x1638;properties.clear();
  flag=false;KernelPatcher disabled;UserVideoDecode::init(disabled);assert(disabled.routes==0&&disabled.solves==0);
  flag=true;KernelPatcher enabled;UserVideoDecode::init(enabled);
  assert(enabled.routes==2&&properties.at("NRedVCNUserEnabled")==1);
  assert(properties.at("NRedDRMVideoRevision")==5);
  assert(devicePatches==(major==23?INIT_SONOMA::findDevice(0x1638):
                         major==24?INIT_SEQUOIA::findDevice(0x1638):INIT_TAHOE::findDevice(0x1638)));
  for(unsigned device:{0x15D8U,0x15DDU,0xFFFF1638U}){
   NRed::device=device;KernelPatcher rejected;UserVideoDecode::init(rejected);assert(rejected.routes==0);
  }
 }
 for(unsigned major:{22U,26U}){
  Version::majorValue=major;NRed::device=0x1638;KernelPatcher rejected;UserVideoDecode::init(rejected);
  assert(rejected.routes==0&&properties.at("NRedVCNUserRejected")==4);
 }
 Version::majorValue=PROFILE_MAJOR;darwinMajor=PROFILE_MAJOR;
 if(!guardLock)guardLock=IOLockAlloc();
 unsigned cases=0;
 // IONew 按原始内存模拟；逐一拒绝两处工作区分配，退出时必须全部释放。
 for(bool dynamicFailure:{false,true}){
  setup(0x1638);auto initial=blocks;
  failTransaction=!dynamicFailure;failDynamic=dynamicFailure;call();
  assert(blocks==initial&&writes==0&&protects==0);
  assert(properties.at("NRedVCNUserLastError")==KERN_RESOURCE_SHORTAGE);
  failTransaction=failDynamic=false;cases++;
 }
 for(unsigned id:{0x15e7U,0x1636U,0x1638U,0x164cU}){
  setup(id);UserVideoDecodeResolver::Plan plan;
  assert(UserVideoDecodeResolver::resolve(reader,base,devicePatches,plan));
  pages(plan.patches,PatchCount);auto initial=blocks;assert(protections.size()>UserVideoDecodeData::PageCount);
  call();if(!properties.contains("NRedVCNUserDynamicApplied")){for(auto[k,v]:properties)fprintf(stderr,"%s=%u\n",k.c_str(),v);}assert(properties.contains("NRedVCNUserDynamicApplied"));rx();assert(writes==PatchCount);
  for(unsigned i=0;i<PatchCount;i++){uint8_t b[UserVideoDecodeData::MaxPatchSize];assert(reader(base+plan.patches[i].offset,b,plan.patches[i].size)==0);assert(memcmp(b,plan.patches[i].patched,plan.patches[i].size)==0);}
  auto before=writes;call();assert(writes==before&&properties.at("NRedVCNUserD07")==1);cases++;
  for(unsigned entry=1;entry<=PatchCount;entry++){
   setup(id);pages(plan.patches,PatchCount);initial=blocks;failWrite=entry;call();assert(blocks==initial);rx();assert(!properties.contains("NRedVCNUserDynamicApplied"));cases++;
  }
  for(unsigned page=1;page<=protections.size();page++){
   setup(id);pages(plan.patches,PatchCount);initial=blocks;failedProtect=page;call();assert(blocks==initial&&writes==0);rx();cases++;
  }
  setup(id);pages(plan.patches,PatchCount);initial=blocks;failedProtectCalls={unsigned(protections.size())+1,unsigned(protections.size())+2};call();assert(blocks==initial);rx();cases++;
 }
 for(unsigned kind=0;kind<12;kind++){
  setup(0x15e7);using namespace UserVideoDecodeData;
  switch(kind){
   case 0:word32(base+452,8193);break;
   case 1:word32(base+448,0xfffffff0);break;
   case 2:word32(imageAddresses[0],0);break;
   case 3:word32(imageAddresses[0]+36,7);break;
   case 4:word32(functionAddresses[0]+100,0);break;
   case 5:word64(base+0x1000+32,imageAddresses[0]-0x200000);word32(base+0x1018+32,0x3000);break;
   case 6:word64(base+232,0xffffffffffffffffULL);break;
   case 7:failedRead=functionAddresses[0];break;
   case 8:{auto&fn=ResolverFunctions[0];std::vector<uint8_t> b(fn.size);assert(reader(functionAddresses[0],b.data(),b.size())==0);
    const auto copy=imageAddresses[0]+0x5e000;put(copy,b.data(),b.size());
    for(unsigned r=0;r<fn.originalCount;r++){auto&ref=fn.originalRefs[r];uint64_t target=0;
     assert(UserSharedCache::relativeTarget(functionAddresses[0],ref.offset,b.data(),target));rel(copy,ref.offset,target);}}break;
   case 9:word64(base+0x7000000,0);break;
   case 10:rel(functionAddresses[0],ResolverFunctions[0].originalRefs[0].offset,base+0x7010000);break;
   case 11:put(base,"invalid",7);break;
  }
  auto initial=blocks;call();assert(blocks==initial&&writes==0&&protects==0);cases++;
 }
#ifndef BASELINE
 // 各 FairPlay 客户端共用解码子集，在重排及跨页缓存中仍对全部已选页实施事务回滚。
 for(unsigned id:{0x15e7U,0x1636U,0x1638U,0x164cU})for(const char* name:{"Music","TV","Other"}){
  clientName=name;
  setup(id);UserVideoDecodeResolver::Plan plan;
  assert(UserVideoDecodeResolver::resolve(reader,base,devicePatches,plan));
  auto enable=[&]{drmClient=true;clientEntitlement=kOSBooleanTrue;pages(plan.patches,PatchCount);};
  std::set<uint64_t> selectedPages;
  constexpr unsigned selectedMask=0x7F87FE;
  for(unsigned i=0;i<PatchCount;i++)if(selectedMask&(1U<<i)){
   const auto& p=plan.patches[i];
   for(auto a=(base+p.offset)&~uint64_t(4095);a<=((base+p.offset+p.size-1)&~uint64_t(4095));a+=4096)selectedPages.insert(a);
  }
  enable();call();rx();
  if(writes!=18||drmTouchedPages!=selectedPages){
   fprintf(stderr,"driver-only id=%x writes=%u touched=%zu expected=%zu\n",id,writes,drmTouchedPages.size(),selectedPages.size());
   for(auto[k,v]:properties)fprintf(stderr,"%s=%u\n",k.c_str(),v);
  }
  assert(writes==18&&drmTouchedPages==selectedPages);
  assert(properties.at("NRedDRMVideoState")==2&&properties.at("NRedDRMVideoPID")==217);
  {SharedRegionCheckArgs empty{0};int value=0;assert(wrappedSharedRegionCheck(reinterpret_cast<void*>(217),&empty,&value)==0&&value==123);}
  assert(properties.at("NRedDRMVideoState")==2);
  assert(properties.at("NRedDRMVideoLastPID")==217&&properties.at("NRedDRMVideoLastStage")==2);
  for(unsigned i=0;i<PatchCount;i++){
   uint8_t b[UserVideoDecodeData::MaxPatchSize];auto& p=plan.patches[i];
   assert(reader(base+p.offset,b,p.size)==0);
   assert(memcmp(b,(selectedMask&(1U<<i))?p.patched:p.original,p.size)==0);
  }
  auto before=writes;call();assert(writes==before&&properties.at("NRedDRMVideoState")==3);cases++;
  for(unsigned entry=1;entry<=18;entry++){
   setup(id);enable();auto initial=blocks;failWrite=entry;call();rx();
   assert(blocks==initial&&properties.at("NRedDRMVideoState")==1);cases++;
  }
  for(unsigned page=1;page<=selectedPages.size();page++){
   setup(id);enable();auto initial=blocks;failedProtect=page;call();rx();
   assert(blocks==initial&&writes==0&&properties.at("NRedDRMVideoState")==1);cases++;
  }
  setup(id);enable();auto initial=blocks;failedProtectCalls={unsigned(selectedPages.size())+1,unsigned(selectedPages.size())+2};
  call();rx();assert(blocks==initial&&properties.at("NRedDRMVideoState")==1);cases++;
  setup(id);enable();put(base+plan.patches[0].offset,plan.patches[0].patched,plan.patches[0].size);initial=blocks;
  call();assert(blocks==initial&&writes==0&&protects==0&&properties.at("NRedDRMVideoState")==1);cases++;
 }
#endif
 // 复用同一公共 Input 时重建映像索引，读取预算累计，不能残留上一轮地址。
 setup(0x15e7);UserSharedCache::Input reused{reader,{}};UserVideoDecodeResolver::Plan reusedPlan;
 for(unsigned i=0;i<2;i++)assert(UserCacheResolver::resolve(reused,base,UserVideoDecodeData::ResolverImages,
 UserVideoDecodeData::ResolverFunctions,devicePatches,UserVideoDecodeData::ResolverLocations,reusedPlan.patches,
 &reusedPlan.original[0][0],&reusedPlan.patched[0][0],UserVideoDecodeData::MaxPatchSize,1));
 // 指针绑定、rel32 溢出与预算耗尽必须拒绝，且解析器本身不写缓存。
 setup(0x15e7);UserSharedCache::Input input{reader,{}};input.reads=32768;UserVideoDecodeResolver::Plan plan;
 assert(!UserCacheResolver::resolve(input,base,UserVideoDecodeData::ResolverImages,UserVideoDecodeData::ResolverFunctions,devicePatches,
 UserVideoDecodeData::ResolverLocations,plan.patches,&plan.original[0][0],&plan.patched[0][0],UserVideoDecodeData::MaxPatchSize));
 printf("PASS %u video dynamic bridge cases: four devices; shifted images/functions/GOT; cross-page rollback; content/metadata/target rejection\n",cases);
}
'''
    if '--vp9' in sys.argv:
        begin = harness.index('int main(){')
        harness = harness[:begin] + r'''
#include "vp9-cache-dynamic.hpp"
template<class Images,class Functions,class Sections,class Slots>
static void checkVP9Install(unsigned major,const Images& images,const Functions& functions,const Sections& sections,
                            const Slots& slots){
 setup(0x1638);vp9Cache(images,functions,sections,slots);cleanBlocks=blocks;darwinMajor=major;
 VP9::state=VP9::State::Off;VCN::vp9Ready=false;
 static UserVP9DecodeResolver::Plan resolved;
 UserSharedCache::Input input{reader,{}};UInt8 scan[UserSharedCache::Input::ScanSize];input.scanBuffer=scan;
 assert(UserVP9DecodeResolver::resolve(input,base,resolved,major));
 pages(resolved.patches,resolved.count);
 VP9::enabled=true;VP9::lock=IOLockAlloc();VP9::plan=IONew(UserVP9DecodeResolver::Plan,1);
 UInt8 uuid[16];assert(reader(base+88,uuid,16)==0);
 auto initial=blocks;
 UserPatchCoordinator::Participant first;
 failedProtect=1;
 VP9::install(map(nullptr),base,uuid,&first);
 assert(first.outcome==UserPatchCoordinator::Outcome::Pending&&!VCN::vp9Ready&&blocks==initial);
 failedProtect=0;
 UserPatchCoordinator::Participant installed;
 VP9::install(map(nullptr),base,uuid,&installed);
 assert(installed.outcome==UserPatchCoordinator::Outcome::Patched&&installed.proofSites==resolved.count&&VCN::vp9Ready);
 rx();auto after=writes;
 assert(properties.at("NRedVTDecoderAdmissionRevision")==1);
 UserPatchCoordinator::Participant already;
 VP9::install(map(nullptr),base,uuid,&already);
 assert(already.outcome==UserPatchCoordinator::Outcome::Patched&&writes==after&&VCN::vp9Ready);
 const auto& site=resolved.patches[0];UInt8 corrupt=site.patched[site.size-1]^1;
 put(base+site.offset+site.size-1,&corrupt,1);
 UserPatchCoordinator::Participant mixed;
 VP9::install(map(nullptr),base,uuid,&mixed);
 assert(mixed.outcome==UserPatchCoordinator::Outcome::Pending&&!VCN::vp9Ready&&writes==after);
 put(base+site.offset,site.patched,site.size);
 uuid[0]^=1;UserPatchCoordinator::Participant foreign;
 VP9::install(map(nullptr),base,uuid,&foreign);
 assert(foreign.outcome==UserPatchCoordinator::Outcome::Pending&&!VCN::vp9Ready&&writes==after);
 // 同一已解析配置恢复干净页面：部分写入必须回滚，并停止本次启动的 VP9 写入。
 blocks=initial;failWrite=writes+1;UserPatchCoordinator::Participant failed;
 VP9::install(map(nullptr),base,VP9::resolvedUUID,&failed);
 assert(failed.outcome==UserPatchCoordinator::Outcome::Pending&&blocks==initial&&!VCN::vp9Ready);
 assert(VP9::state==VP9::State::Failed);rx();
 IODelete(VP9::plan,UserVP9DecodeResolver::Plan,1);
 VP9::plan=nullptr;
 IOLockFree(VP9::lock);
 VP9::lock=nullptr;
 printf("PASS VP9 install Darwin %u: %u proof sites, retry, mixed/UUID rejection, rollback\n",major,resolved.count);
}
int main(){
 namespace Sonoma=UserVP9DecodeSonomaData;
 namespace Sequoia=UserVP9DecodeSequoiaData;
 checkVP9Install(23,Sonoma::ResolverImages,Sonoma::ResolverFunctions,Sonoma::SectionTargets,Sonoma::ABISlots);
 checkVP9Install(24,Sequoia::ResolverImages,Sequoia::ResolverFunctions,Sequoia::SectionTargets,Sequoia::ABISlots);
 checkVP9Install(25,ResolverImages,ResolverFunctions,SectionTargets,ABISlots);
}
'''
    if LEGACY:
        if SONOMA:
            harness = harness.replace('cache(ResolverImages,ResolverFunctions,true)', 'cache(ResolverImages,ResolverFunctions,false)')
            harness = harness.replace('UserVideoDecodeData::MaxPatchSize,1)', 'UserVideoDecodeData::MaxPatchSize,0)')
        else:
            harness = harness.replace('cache(ResolverImages,ResolverFunctions,true)',
                'cache(ResolverImages,ResolverFunctions,true,SectionTargets,std::size(SectionTargets))')
            harness = harness.replace('UserVideoDecodeData::MaxPatchSize,1)',
                'UserVideoDecodeData::MaxPatchSize,true,UserVideoDecodeData::SectionTargets,std::size(UserVideoDecodeData::SectionTargets))')
        harness = harness.replace('UserVideoDecodeData::', 'UserSequoiaVideoData::')
        harness = harness.replace('using namespace UserVideoDecodeData;', 'using namespace UserSequoiaVideoData;')
        harness = harness.replace('devicePatches,plan)', f'devicePatches,plan,{23 if SONOMA else 24})')
        harness = harness.replace('0x7F87FE', '0x7FE1FE')
        harness = harness.replace('drmClient=false;clientEntitlement', f'darwinMajor={23 if SONOMA else 24};Version::majorValue={23 if SONOMA else 24};drmClient=false;clientEntitlement')
    if SONOMA:
        harness = harness.replace('UserSequoiaVideoData', 'UserSonomaVideoData')
    # 从实际源文件中提取的桥接引用进程诊断函数。
    uuid = ('UserSonomaVideoData::CacheUUID' if SONOMA else 'UserSequoiaVideoData::CacheUUID') if LEGACY else 'UserVideoDecodeData::CacheUUID'
    harness = harness.replace('BODY', 'static bool referenceUUID=false;\nBODY')
    harness = harness.replace('cleanBlocks=blocks;', f'if(referenceUUID)put(base+88,{uuid},16);cleanBlocks=blocks;')
    harness = harness.replace('int main(){', 'int main(int argc,char**){referenceUUID=argc>1;')
    harness = harness.replace('INIT_SONOMA', 'UserSonomaVideoData').replace('INIT_SEQUOIA', 'UserSequoiaVideoData').replace('INIT_TAHOE', 'UserVideoDecodeData')
    harness = harness.replace('PROFILE_MAJOR', str(23 if SONOMA else 24 if LEGACY else 25))
    harness = harness.replace('BODY', body + '\n' + source[source.index('void UserVideoDecode::init('):])
    harness = harness.replace('static kern_return_t vm_protect', 'static int proc_pid(proc_t);static void proc_name(int,char*,int);\nstatic kern_return_t vm_protect', 1)
    if '--baseline' in sys.argv:
        harness = harness.replace('call();if(!properties.contains("NRedVCNUserDynamicApplied"))', 'call();assert(writes==0&&protects==0);puts("PASS baseline reproduces unknown-cache rejection");return 0;if(!properties.contains("NRedVCNUserDynamicApplied"))',1)
    file, exe = WORK / 'test.cpp', WORK / 'test'
    file.write_text(harness)
    flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if '--sanitize' in sys.argv else []
    if '--baseline' in sys.argv:
        flags.append('-DBASELINE')
    subprocess.run(['clang++', '-std=c++23', '-O2', *flags, '-I'+str(WORK/'stubs'),
                    '-I'+str(ROOT/'NootedRed'), '-I'+str(ROOT/'tools/fixtures'), str(file), '-o', str(exe)], check=True, timeout=60)
    if '--known-uuid' not in sys.argv:
        subprocess.run([str(exe)], check=True, timeout=60)
    subprocess.run([str(exe), '--known-uuid'], check=True, timeout=60)


if __name__ == '__main__':
    main()
