"""编译实际 Metal/VCN 桥接，注入旧系统缓存与提交守卫失败路径；不访问 GPU。"""
import argparse
from pathlib import Path
from user_patch_memory_harness import with_memory_helpers
from import_legacy_compat import write_fixture
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/legacy-bridges'

COMMON = r'''
#include <algorithm>
#include <array>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <vector>
#include <optional>
#include <PenguinWizardry/KernelVersion.hpp>
#include <UserSurfaceSyncResolver.hpp>
#include <UserVideoDecodeResolver.hpp>
#include <UserSubmitGuardData.hpp>
#include <UserLegacyData.hpp>
#include <VCNCapabilities.hpp>
#include <VPF/CapabilityState.hpp>
#include <VPF/DependencyPlanner.hpp>
#include <VPF/DisplayRuntime.hpp>
#include <VPF/PatchManifestData.hpp>
#include <VPF/ExecutableTrampolineResource.hpp>
#include <VPF/MetalTransactionExecutor.hpp>
#include <VPF/ResolverAdapters.hpp>
#include <VPF/TransactionExecutor.hpp>
#include <VPF/VerifiedPatchPlan.hpp>
using user_addr_t=uint64_t;using mach_vm_address_t=uint64_t;using mach_vm_size_t=uint64_t;
using vm_map_t=void*;using task_t=void*;using proc_t=void*;using natural_t=unsigned;
using mach_msg_type_number_t=unsigned;using vm_prot_t=int;using kern_return_t=int;using vm_address_t=uint64_t;
struct vm_region_submap_short_info_data_64_t {int protection=0,max_protection=0,is_submap=0;};using vm_region_recurse_info_t=int*;
constexpr unsigned PAGE_SIZE=4096,VM_REGION_SUBMAP_SHORT_INFO_COUNT_64=12;
constexpr int TRUE=1,FALSE=0,KERN_SUCCESS=0,KERN_INVALID_ADDRESS=1,KERN_PROTECTION_FAILURE=2,KERN_INVALID_ARGUMENT=4,KERN_FAILURE=5,KERN_RESOURCE_SHORTAGE=3,VM_FLAGS_ANYWHERE=1;
constexpr int VM_PROT_NONE=0,VM_PROT_READ=1,VM_PROT_WRITE=2,VM_PROT_EXECUTE=4,VM_PROT_COPY=16;
#define SYSLOG(...) ((void)0)
#define IONew(T,N) new T[N]
#define IODelete(P,T,N) delete[] P
struct IOLock {bool locked=false;};
static IOLock* IOLockAlloc(){return new IOLock;}
static void IOLockFree(IOLock* p){delete p;}
static void IOLockLock(IOLock* p){assert(!p->locked);p->locked=true;}
static void IOLockUnlock(IOLock* p){assert(p->locked);p->locked=false;}
constexpr int THREAD_UNINT=0;
static int IOLockSleep(IOLock*,void*,int){assert(false);return 0;}
static void IOLockWakeup(IOLock*,void*,bool){}
static void* current_thread(){static thread_local char identity;return &identity;}
#include <UserPatchMapGate.hpp>
namespace UserPatchCoordinator {
using PatchCallback=int(*)(void*,void*,int);
inline bool deferModulePatches(){return false;}
inline void clearDisplay(){}
inline void clearVideo(){}
inline void registerDisplay(PatchCallback){}
inline void registerVideo(PatchCallback){}
}
namespace Buffer {template<class T>T* create(size_t n){return new T[n];}template<class T>void deleter(T* p){delete[] p;}}
template<typename T,size_t N>constexpr size_t arrsize(const T(&)[N]){return N;}
static std::map<std::string,unsigned> properties;
struct NRed {unsigned device=0x1638;static NRed& singleton(){static NRed n;return n;}unsigned getDeviceID(){return device;}void setProp32(const char* k,unsigned v){properties[k]=v;}};
static unsigned hostMajor=23;
const PenguinWizardry::KernelVersion& currentKernelVersion(){static std::optional<PenguinWizardry::KernelVersion> v;return v.emplace(hostMajor,6);}
static bool wantRender=true,fairPlay=false;
namespace UserPatchCoordinator {
inline UInt32 requestedDisplayGroups(){return VPF::requestedGroupsForDisplayFix(wantRender,hostMajor);}
}
static bool checkKernelArgument(const char* s){
 return strcmp(s,"-NRedVideoFix")==0 || (wantRender && strcmp(s,"-NRedDisplayFix")==0);
}
namespace UserPatchPolicy {static bool isFairPlayClient(task_t){return fairPlay;}}
namespace VCN {static bool allocated(){return true;}}
namespace UserSurfaceSync {void init(struct KernelPatcher&);}
struct KernelPatcher{
 static constexpr int KernelID=0;unsigned routes=0;
 struct RouteRequest{template<typename T,typename O>RouteRequest(const char*,T,O&) {}};
 uint64_t solveSymbol(int,const char*){return 1;}void clearError(){}bool routeMultipleLong(int,RouteRequest*,int){routes++;return true;}
};
static task_t current_task(){return reinterpret_cast<void*>(1);}
static int proc_pid(proc_t){return 217;}
static void proc_name(int,char* p,int n){snprintf(p,n,"WindowServer");}
static constexpr uint64_t cacheBase=0x7ff800200000ULL;
static constexpr uint64_t testSubmitOffset=0x500000;
static std::array<uint8_t,104> cacheHeader;
static std::map<uint64_t,std::array<uint8_t,4096>> memory;
static std::map<uint64_t,int> protections,maximums;
static std::set<unsigned> failProtect,failWrite;
static unsigned protects=0,writes=0,allocations=0,deallocations=0;
static bool failAllocate=false;
static uint64_t failRead=0;
static constexpr uint64_t allocatedPage=0x600000;
static constexpr uint64_t guardAddress=0x500000;
static std::array<uint8_t,4096> guardPage;
static int reader(uint64_t a,void* p,size_t n){
 if(a==0x1000){memcpy(p,&cacheBase,n);return 0;}
 if(a==cacheBase&&n==cacheHeader.size()){memcpy(p,cacheHeader.data(),n);return 0;}
 if(a==guardAddress){assert(n<=guardPage.size());memcpy(p,guardPage.data(),n);return 0;}
 auto i=memory.find(a&~uint64_t(4095));if(a==failRead||i==memory.end()||n>4096-(a&4095))return 1;
 memcpy(p,i->second.data()+(a&4095),n);return 0;
}
static int writer(const void* p,uint64_t a,size_t n){
 writes++;assert(protections.at(a&~uint64_t(4095))==3);assert(n<=4096-(a&4095));
 auto& page=memory.at(a&~uint64_t(4095));
 memcpy(page.data()+(a&4095),p,failWrite.contains(writes)?std::min(n,size_t(2)):n);
 return failWrite.contains(writes)?1:0;
}
static kern_return_t vm_protect(vm_map_t,uint64_t a,unsigned,int maximum,int flags){
 protects++;if(failProtect.contains(protects))return 42;
 assert(memory.contains(a));if(maximum){maximums[a]=flags&7;protections[a]&=flags&7;}
 else{protections[a]=flags&7;maximums[a]|=flags&7;}return 0;
}
static kern_return_t vm_allocate(vm_map_t,vm_address_t* a,unsigned,int){
 if(failAllocate)return KERN_RESOURCE_SHORTAGE;assert(!memory.contains(allocatedPage));
 allocations++;*a=allocatedPage;memory[*a]={};protections[*a]=3;maximums[*a]=7;return 0;
}
static kern_return_t vm_deallocate(vm_map_t,uint64_t a,unsigned){
 deallocations++;assert(memory.erase(a)==1);protections.erase(a);maximums.erase(a);return 0;
}
BODY
INIT
static int original(proc_t,SharedRegionCheckArgs*,int* v){*v=123;return 0;}
static vm_map_t map(task_t){return reinterpret_cast<void*>(1);}
static kern_return_t query(vm_map_t,uint64_t* a,uint64_t* n,unsigned*,int* p,unsigned*){
 auto i=protections.find(*a);if(i==protections.end())return 1;
 auto* info=reinterpret_cast<vm_region_submap_short_info_data_64_t*>(p);info->is_submap=0;info->protection=i->second;*n=4096;return 0;
}
static void reset(const UserLegacyCompat::Profile* profile){
 memory.clear();protections.clear();maximums.clear();properties.clear();failProtect.clear();failWrite.clear();
 protects=writes=allocations=deallocations=0;failRead=0;failAllocate=fairPlay=false;
 published={};seenStages=appliedCount=errorCount=0;
 cacheHeader.fill(0);memcpy(cacheHeader.data(),"dyld_v1 x86_64h",16);
 memcpy(cacheHeader.data()+88,profile?profile->uuid:UserSurfaceSyncData::CacheUUID,16);
 if(profile)for(unsigned i=0;i<profile->pageCount;i++){
  auto a=cacheBase+profile->pages[i].offset;memcpy(memory[a].data(),profile->pages[i].original,4096);protections[a]=maximums[a]=5;
 }
 const auto method=cacheBase+(profile?UserLegacyData::submitOffset(currentKernelVersion().major()):
                                         testSubmitOffset);
 const auto methodPage=method&~uint64_t(4095);
 memory[methodPage]={};protections[methodPage]=maximums[methodPage]=5;
 uint8_t payload[sizeof(UserSubmitGuardData::Payload)];memcpy(payload,UserSubmitGuardData::Payload,sizeof(payload));
 const uint64_t continuation=method+UserSubmitGuardData::MethodPatchSize;
 memcpy(payload+UserSubmitGuardData::PayloadJumpOffset,&continuation,sizeof(continuation));
 guardPage.fill(0);memcpy(guardPage.data(),payload,sizeof(payload));
 uint8_t entry[15]={0xff,0x25,0,0,0,0};memcpy(entry+6,&guardAddress,sizeof(guardAddress));entry[14]=0x90;
 memcpy(memory[methodPage].data()+(method&4095),entry,sizeof(entry));
 protections[guardAddress]=maximums[guardAddress]=5;
 originalCheck=reinterpret_cast<uint64_t>(original);getTaskMap=map;queryRegion=query;readUser=reader;writeUser=writer;
}
static void call(){SharedRegionCheckArgs a{0x1000};int v=0;assert(wrappedSharedRegionCheck(reinterpret_cast<void*>(217),&a,&v)==0&&v==123);}
static void rx(){for(auto [a,p]:protections){assert(p==5&&maximums.at(a)==5);}}
static void check(const UserLegacyCompat::Profile* profile,bool patched){
 for(unsigned i=0;i<profile->patchCount;i++){const auto& p=profile->patches[i];uint8_t b[512];assert(p.size<=512);
 assert(reader(cacheBase+p.offset,b,p.size)==0&&memcmp(b,patched?p.patched:p.original,p.size)==0);}
}
MAIN
'''

RENDER = r'''
int main(){
 for(unsigned major:{23U})for(unsigned device:DEVICES){
  hostMajor=major;NRed::singleton().device=device;
  const auto* profile=PROFILE;assert(profile);
  KernelPatcher kernel;MODULE::init(kernel);assert(kernel.routes==1);
  SUBMIT_OFF
  reset(profile);call();check(profile,true);rx();unsigned before=writes;call();assert(writes==before);
  for(unsigned failure=1;failure<=profile->patchCount;failure++){
   reset(profile);auto original=memory;failWrite={failure};call();assert(memory==original);check(profile,false);rx();
  }
  for(unsigned page=1;page<=profile->pageCount;page++){
   reset(profile);auto original=memory;failProtect={page};call();assert(memory==original);rx();
  }
  reset(profile);auto original=memory;failProtect={profile->pageCount+1,profile->pageCount+2};call();assert(memory==original);rx();
  for(unsigned page=0;page<profile->pageCount;page++){
   reset(profile);auto pristine=memory;failProtect={profile->pageCount+2+2*page};call();
   assert(memory==pristine);rx();
  }
  reset(profile);cacheHeader[88]^=1;call();assert(writes==0&&protects==0);
  reset(profile);memory.begin()->second[0]^=1;call();assert(writes==0&&protects==0);
  reset(profile);failRead=cacheBase+profile->pages[0].offset+256;call();assert(writes==0&&protects==0);
 }
 puts("PASS actual MODULE Sonoma bridge: full pages, every write/page failure, rollback and idempotency");
 GUARD
}
'''

GUARD = r'''
 for(unsigned major:{23U,25U}){
  hostMajor=major;wantRender=true;KernelPatcher kernel;UserSurfaceSync::init(kernel);
  assert(kernel.routes==1&&properties.at("NRedSubmitGuardEnabled")==1&&renderRequested);
  const auto* profile=UserLegacyData::metal(major);
  const uint64_t method=cacheBase+(profile?UserLegacyData::submitOffset(major):testSubmitOffset);
  const auto setup=[&]{reset(profile);auto a=method&~uint64_t(4095);memory[a]={};protections[a]=maximums[a]=5;
   memcpy(memory[a].data()+(method&4095),UserSubmitGuardData::MethodOriginal,UserSubmitGuardData::MethodPatchSize);};
  const auto entryOriginal=[&]{uint8_t b[15];assert(reader(method,b,15)==0);return memcmp(b,UserSubmitGuardData::MethodOriginal,15)==0;};
  const auto installGuard=[&](uint64_t offset,bool& fresh){
   SubmitGuardWorkspace workspace{};VPF::DisplayRuntime<> runtime;
   runtime.reset(reinterpret_cast<UInt64>(map(nullptr)),major,NRed::singleton().getDeviceID(),
                 VPF::displayGroupBit(VPF::DisplayGroup::MetalSubmitSafety));
   runtime.verify(0,VPF::ExternalMetalBridge);
   return installSubmitGuard(map(nullptr),cacheBase,fresh,workspace,runtime,offset);};
  setup();bool fresh=false;assert(installGuard(method-cacheBase,fresh)==KERN_SUCCESS&&fresh);
  rx();assert(allocations==1&&deallocations==0&&!entryOriginal());auto before=writes;
  fresh=true;assert(installGuard(method-cacheBase,fresh)==KERN_SUCCESS&&!fresh);assert(writes==before&&allocations==1);
  memory.at(allocatedPage)[100]^=1;fresh=false;assert(installGuard(method-cacheBase,fresh)!=KERN_SUCCESS&&!fresh);
  assert(writes==before);
  setup();failAllocate=true;fresh=false;assert(installGuard(method-cacheBase,fresh)!=KERN_SUCCESS);
  assert(allocations==0&&entryOriginal());rx();
  for(unsigned failure:{1U,2U}){setup();failWrite={failure};fresh=false;
   assert(installGuard(method-cacheBase,fresh)!=KERN_SUCCESS);assert(entryOriginal()&&allocations==deallocations);rx();}
  for(unsigned failure:{1U,2U,3U,5U}){setup();failProtect={failure};fresh=false;
   assert(installGuard(method-cacheBase,fresh)!=KERN_SUCCESS);assert(entryOriginal()&&allocations==deallocations);rx();}
  setup();failProtect={4,5};fresh=false;assert(installGuard(method-cacheBase,fresh)!=KERN_SUCCESS);
  assert(entryOriginal()&&allocations==deallocations);rx();
  setup();failWrite={2,3,4};fresh=false;assert(installGuard(method-cacheBase,fresh)!=KERN_SUCCESS);
  assert(allocations==1&&deallocations==0&&properties.at("NRedSubmitGuardRollbackFailed")==1);rx();
 }
 hostMajor=24;KernelPatcher sequoiaKernel;UserSurfaceSync::init(sequoiaKernel);
 assert(sequoia&&computeScratch&&srdShared&&!legacyProfile&&sequoiaKernel.routes==1);
 puts("PASS actual SubmitGuard: Sonoma/Tahoe transaction; Sequoia dynamic mode entry; full trampoline identity, failure rollback, RX/max, allocation lifetime");
'''

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sanitize', action='store_true')
    args = parser.parse_args()
    stubs = WORK / 'stubs/IOKit'
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt16=uint16_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    util = WORK / 'stubs/Headers'
    util.mkdir(parents=True, exist_ok=True)
    (util / 'kern_util.hpp').write_text('#pragma once\n#include <IOKit/IOTypes.h>\n')
    fixture = write_fixture()
    for module in ['UserSurfaceSync', 'UserVideoDecode']:
        source = (ROOT / f'NootedRed/{module}.cpp').read_text()
        body = re.split(r'namespace\s*\{', source, maxsplit=1)[1]
        body = re.split(r'}\s*// namespace', body, maxsplit=1)[0]
        body = with_memory_helpers(body)
        init = source[source.index(f'void {module}::init('):]
        if module == 'UserSurfaceSync':
            main_code = RENDER.replace('MODULE', module).replace('PROFILE', 'UserLegacyData::metal(major)')
            main_code = main_code.replace('DEVICES', '{0x15D8U,0x15DDU,0x15E7U,0x1636U,0x1638U,0x164CU}')
            main_code = main_code.replace('GUARD', GUARD).replace('SUBMIT_OFF', 'submitGuard=true;')
        else:
            main_code = r'''
int main(){
 hostMajor=23;
 for(unsigned device:{0x15E7U,0x1636U,0x1638U,0x164CU})for(bool drm:{false,true}){
  NRed::singleton().device=device;KernelPatcher kernel;UserVideoDecode::init(kernel);
  assert(kernel.routes==0&&!devicePatches&&properties.at("NRedVCNUserRejected")==6);
  assert(properties.at("NRedVCNUserUnsupportedOS")==1);
  // 离线历史 Profile 只用于布置真实 Sonoma 页面；生产入口必须在安装 Hook 前拒绝。
  auto* profile=UserLegacyFixture::video(23,device);assert(profile);reset(profile);fairPlay=drm;
  auto pristine=memory;call();assert(memory==pristine&&writes==0&&protects==0&&allocations==0);
  assert(!properties.contains("NRedVCNUserApplied"));
 }
 puts("PASS actual Sonoma video entry: all four devices and both FairPlay states reject the unsupported OS without memory writes");
}
'''
            body = '#include <UserLegacyVideoFixture.hpp>\n' + body
        text = COMMON.replace('BODY', body).replace('INIT', init).replace('MAIN', main_code)
        text = text.replace('struct vm_region_submap_short_info_data_64_t {int protection=0,max_protection=0,is_submap=0;};',
                            'struct vm_region_submap_short_info_data_64_t {int protection=0,max_protection=0,is_submap=0;'
                            'unsigned object_id=0;unsigned char share_mode=0;unsigned short shadow_depth=0;};')
        text = text.replace('namespace UserSurfaceSync {void init(struct KernelPatcher&);}',
                            f'struct KernelPatcher;namespace {module} {{void init(KernelPatcher&);}}')
        path, exe = WORK / f'{module}.cpp', WORK / module
        path.write_text(text)
        flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if args.sanitize else []
        # 只有 Video 桥接需要离线历史头；Metal 桥接只能看到生产数据。
        include = ['-I'+str(fixture)] if module == 'UserVideoDecode' else []
        subprocess.run(['xcrun', 'clang++', '-std=c++23', '-O2', *flags, '-I'+str(WORK / 'stubs'),
                        '-I'+str(ROOT / 'NootedRed'), *include, str(path), '-o', str(exe)], check=True, timeout=60)
        subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
