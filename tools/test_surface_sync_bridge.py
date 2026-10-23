"""编译实际内核桥接代码，覆盖多页拒绝、部分写入回滚和保护恢复。"""
from pathlib import Path
from user_patch_memory_harness import with_memory_helpers
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/user-surface-sync/bridge"


def main():
    source = (ROOT / "NootedRed/UserSurfaceSync.cpp").read_text()
    assert "HDR" not in source and "Transfer1x1" not in source
    nred = (ROOT / "NootedRed/NRed.cpp").read_text().split("switch (this->deviceID)", 1)[1].split("default: PANIC", 1)[0]
    devices = {int(value, 16) for value in re.findall(r"case (0x[0-9A-Fa-f]+):", nred)}
    header = (ROOT / "NootedRed/UserSurfaceSyncData.hpp").read_text().split("DevicePatches[] = {", 1)[1].split("};", 1)[0]
    assert devices == {int(value, 16) for value in re.findall(r"\{(0x[0-9A-Fa-f]+),", header)}, 'PCI 表与原作者分类不同步'
    body = source[source.index("namespace {") + len("namespace {"):source.index("} // namespace")]
    body = with_memory_helpers(body)
    init = source[source.index("void UserSurfaceSync::init("):]
    stub = WORK / "stubs/IOKit"
    stub.mkdir(parents=True, exist_ok=True)
    (stub / "IOTypes.h").write_text("#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n")
    util = WORK / "stubs/Headers"
    util.mkdir(parents=True, exist_ok=True)
    (util / "kern_util.hpp").write_text('#pragma once\n#include <IOKit/IOTypes.h>\n')
    harness = r'''
#include <cassert>
#include <cstring>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <set>
#include <string>
#include <array>
#include <optional>
#include <vector>
#include <PenguinWizardry/KernelVersion.hpp>
#include <UserSurfaceSyncData.hpp>
#include <UserSubmitGuardData.hpp>
#include <UserSurfaceSyncResolver.hpp>
#include <UserLegacyData.hpp>
using user_addr_t=uint64_t;using mach_vm_address_t=uint64_t;using mach_vm_size_t=uint64_t;
using vm_map_t=void*;using task_t=void*;using proc_t=void*;using natural_t=unsigned;
using mach_msg_type_number_t=unsigned;using vm_prot_t=int;using kern_return_t=int;
struct vm_region_submap_short_info_data_64_t {int protection=0,max_protection=0,is_submap=0;};using vm_region_recurse_info_t=int*;
constexpr unsigned PAGE_SIZE=4096,VM_REGION_SUBMAP_SHORT_INFO_COUNT_64=12;
constexpr int TRUE=1,FALSE=0,KERN_SUCCESS=0,KERN_INVALID_ADDRESS=1,KERN_PROTECTION_FAILURE=2,KERN_INVALID_ARGUMENT=4,KERN_FAILURE=5;
constexpr int VM_FLAGS_ANYWHERE=1,KERN_RESOURCE_SHORTAGE=3;
static bool failWorkspaceAllocation=false;
static unsigned liveWorkspaces=0;
template<class T>T* testNew(size_t n){if(failWorkspaceAllocation)return nullptr;auto* p=new T[n];liveWorkspaces++;return p;}
template<class T>void testDelete(T* p){assert(liveWorkspaces>0);liveWorkspaces--;delete[] p;}
#define IONew(T,N) ({ testNew<T>(N); })
#define IODelete(P,T,N) testDelete(P)
using vm_address_t=uint64_t;
constexpr int VM_PROT_NONE=0,VM_PROT_READ=1,VM_PROT_WRITE=2,VM_PROT_EXECUTE=4,VM_PROT_COPY=16;
#define SYSLOG(...) ((void)0)
struct Patch {}; // Lilu 的同名全局类型；桥接代码必须限定自己的 Patch。
template<typename T,size_t N>constexpr size_t arrsize(const T(&)[N]){return N;}
static std::map<std::string,unsigned> properties;
static unsigned property(const char* key){auto it=properties.find(key);if(it==properties.end()){fprintf(stderr,"missing property: %s\n",key);abort();}return it->second;}
struct NRed {static NRed& singleton(){static NRed n;return n;}void setProp32(const char* k,unsigned v){properties[k]=v;}
 unsigned getDeviceID(){return device; }static unsigned device;};unsigned NRed::device=0x1638;
struct Version{static unsigned majorValue,minorValue;};unsigned Version::majorValue=25;unsigned Version::minorValue=6;
const PenguinWizardry::KernelVersion& currentKernelVersion(){static std::optional<PenguinWizardry::KernelVersion> current;return current.emplace(Version::majorValue,Version::minorValue);}
static bool flag=true,candidateFlag=false,legacyFlag=false,transferFlag=false,scratchFlag=false;
static bool checkKernelArgument(const char* s){assert(strcmp(s,"-NRedDisplayFix")==0);return flag;}
struct KernelPatcher{
 static constexpr int KernelID=0;int solves=0,routes=0;bool missing=false,routeOK=true;
 struct RouteRequest{template<typename T>RouteRequest(const char*,T,uint64_t&) {}};
 uint64_t solveSymbol(int,const char*){solves++;return missing?0:1;}void clearError(){}
 bool routeMultipleLong(int,RouteRequest*,int count){assert(count==1);routes++;return routeOK;}
};namespace UserSurfaceSync{void init(KernelPatcher&);}
static task_t current_task(){return reinterpret_cast<void*>(1);}
static bool isWindowServer=false,isWeather=false;
static int proc_pid(proc_t p){return int(reinterpret_cast<uintptr_t>(p));}
static void proc_name(int,char* name,int size){snprintf(name,size,"%s",isWeather?"Weather":(isWindowServer?"WindowServer":"Other"));}
static std::map<uint64_t,int> protection,maxProtection;
static std::set<int> failProtect;
static int protectCalls=0,writeCalls=0,failWrite=0,originalResult=0,regionMode=0;
static bool invalidCache=false,noMap=false;
static uint64_t base=UserSurfaceSyncData::CacheBase+0x100000;
constexpr auto BaseN=arrsize(UserSurfaceSyncData::ComputeScratchPatches);
constexpr auto MaxN=BaseN;
static size_t N=2;
static std::array<std::array<uint8_t,512>,MaxN> code;
static bool withdrawnCode=false;
static std::vector<size_t> writeOrder;
static kern_return_t vm_allocate(vm_map_t,vm_address_t* page,unsigned,int){*page=0;return KERN_FAILURE;}
static kern_return_t vm_deallocate(vm_map_t,vm_address_t,unsigned){return KERN_SUCCESS;}
static kern_return_t vm_protect(vm_map_t,uint64_t address,unsigned,int setMax,vm_prot_t flags){
 protectCalls++;if(failProtect.contains(protectCalls))return 42;
 assert(protection.contains(address));const int value=flags&7;
 if(setMax){maxProtection[address]=value;protection[address]&=value;}else{protection[address]=value;maxProtection[address]|=value;}return 0;
}
BODY
INIT
static const auto* active(){return activePatches();}
static auto fixture(size_t i){return UserSurfaceSyncData::ComputeScratchPatches[i];}
static int original(proc_t,SharedRegionCheckArgs*,int* value){assert(liveWorkspaces==0);*value=123;return originalResult;}
static vm_map_t map(task_t){return noMap?nullptr:reinterpret_cast<void*>(2);}
static kern_return_t query(vm_map_t,uint64_t* address,uint64_t* size,unsigned* depth,int* data,unsigned* count){
 assert(*count==VM_REGION_SUBMAP_SHORT_INFO_COUNT_64);
 if(regionMode==1)return 42;
 auto* info=reinterpret_cast<vm_region_submap_short_info_data_64_t*>(data);*size=PAGE_SIZE;
 info->is_submap=*depth==0;
 if(*depth==0)info->protection=1;else if(protection.contains(*address))info->protection=protection.at(*address);
 else{fprintf(stderr,"query page missing: address=%llx depth=%u, known pages:",(unsigned long long)*address,*depth);for(auto [page,flags]:protection)fprintf(stderr," %llx=%d",(unsigned long long)page,flags);fputc('\n',stderr);abort();}
 if(*depth==0)info->max_protection=5;else info->max_protection=maxProtection.at(*address);
 if(regionMode==2)*address+=PAGE_SIZE;if(regionMode==3)*count=1;if(regionMode==4)*size=PAGE_SIZE-1;
 return 0;
}
static int reader(uint64_t address,void* p,size_t size){
 if(address==0x1000){memcpy(p,&base,8);return 0;}
 if(address==base){memset(p,0,size);memcpy(p,UserSurfaceSyncData::CacheMagic,16);memcpy(static_cast<uint8_t*>(p)+88,UserSurfaceSyncData::CacheUUID,16);
  if(invalidCache)static_cast<uint8_t*>(p)[88]^=1;return 0;}
 for(size_t i=0;i<MaxN;i++){auto record=fixture(i);if(address==base+record.offset && size==record.size){memcpy(p,code[i].data(),size);return 0;}}
 for(auto& entry:UserSurfaceSyncData::WithdrawnTransferGuards){
  if(address==base+entry.offset){assert(size==entry.size);memcpy(p,entry.original,size);if(withdrawnCode)static_cast<uint8_t*>(p)[0]^=1;return 0;}}
 return 1;
}
static int writer(const void* p,uint64_t address,size_t size){
 writeCalls++;
 auto page=address&~uint64_t(4095);if(!protection.contains(page)){fprintf(stderr,"write page missing: %llx\n",(unsigned long long)page);abort();}assert(protection.at(page)==3);
 for(size_t i=0;i<N;i++)if(address==base+active()[i].offset){
  writeOrder.push_back(i);
  if(writeCalls==failWrite){memcpy(code[i].data(),p,2);return 1;}memcpy(code[i].data(),p,size);return 0;}
 assert(false);return 1;
}
static void reset(){
 assert(liveWorkspaces==0);failWorkspaceAllocation=false;
 protection.clear();maxProtection.clear();properties.clear();failProtect.clear();protectCalls=writeCalls=failWrite=originalResult=regionMode=0;
 writeOrder.clear();
 invalidCache=noMap=withdrawnCode=false;seenStages=appliedCount=errorCount=windowServerCount=weatherCount=0;isWindowServer=isWeather=false;
 for(size_t i=0;i<MaxN;i++){auto p=fixture(i);memcpy(code[i].data(),p.original,p.size);if(i<N){const auto page=(base+p.offset)&~uint64_t(4095);protection[page]=5;maxProtection[page]=5;}}
 originalCheck=reinterpret_cast<uint64_t>(original);getTaskMap=map;queryRegion=query;readUser=reader;writeUser=writer;
}
static void call(proc_t process=nullptr){SharedRegionCheckArgs args{0x1000};int value=0;assert(wrappedSharedRegionCheck(process,&args,&value)==originalResult && value==123);assert(liveWorkspaces==0);}
static void bytes(bool patched){for(size_t i=0;i<N;i++){auto& p=active()[i];assert(memcmp(code[i].data(),patched?p.patched:p.original,p.size)==0);}}
static void rx(){for(auto [a,p]:protection){assert(p==5);assert(maxProtection.at(a)==5);}}
int main(int argc,char** argv){
 for(bool enabled:{false,true}){
  flag=enabled;KernelPatcher p;UserSurfaceSync::init(p);assert(p.routes==int(enabled));
  assert(submitGuard==enabled && renderRequested==enabled && legacyBlend==enabled);
  assert(computeScratch==enabled && srdShared==enabled);
 }
 flag=true;
 for(auto& entry:UserSurfaceSyncData::DevicePatches)for(unsigned major:{23U,24U,25U})for(unsigned minor:{0U,1U,6U,7U,99U}){
  NRed::device=entry.deviceID;Version::majorValue=major;Version::minorValue=minor;properties.clear();
  KernelPatcher accepted;UserSurfaceSync::init(accepted);assert(accepted.routes==1);
  assert(submitGuard && legacyBlend && renderRequested);
  assert(computeScratch==(major==25) && srdShared==(major==25));
  assert(properties.at("NRedDisplayFixEnabled")==1 && properties.at("NRedSubmitGuardEnabled")==1);
 }
 NRed::device=0x1638;Version::minorValue=6;
 for(unsigned major:{19U,22U,26U}){
  Version::majorValue=major;properties.clear();KernelPatcher wrong;UserSurfaceSync::init(wrong);
  assert(wrong.solves==0 && properties.at("NRedImmediateSyncRejected")==4);
 }
 Version::majorValue=25;
 for(unsigned id:{0x1637U,0x164DU,0x15D9U,0x15DEU,0x6863U,0U,0xFFFF15E7U}){
  NRed::device=id;properties.clear();KernelPatcher rejected;UserSurfaceSync::init(rejected);
  assert(rejected.solves==0 && properties.at("NRedImmediateSyncRejected")==3);
 }
 NRed::device=0x1638;
 for(unsigned id=0;id<=0xFFFF;id++){
  static const std::set<unsigned> supportedIDs{SUPPORTED_IDS};bool supported=supportedIDs.contains(id);
  assert((UserSurfaceSyncData::findDevice(id)!=nullptr)==supported);
 }
 KernelPatcher missing;missing.missing=true;UserSurfaceSync::init(missing);assert(missing.routes==0 && properties.at("NRedImmediateSyncRejected")==1);
 submitGuard=false;
scratchFlag=argc>1&&strcmp(argv[1],"--scratch")==0;candidateFlag=argc>1&&strcmp(argv[1],"--immediate")!=0;transferFlag=argc>1&&strcmp(argv[1],"--transfer")==0;legacyFlag=transferFlag||scratchFlag||(argc>1&&strcmp(argv[1],"--legacy")==0);srdShared=candidateFlag;legacyBlend=legacyFlag;computeScratch=transferFlag||scratchFlag;N=computeScratch?BaseN:(legacyFlag?3:2);
 NRed::device=argc>2?unsigned(strtoul(argv[2],nullptr,0)):0x1638;devicePatches=UserSurfaceSyncData::findDevice(NRed::device);assert(devicePatches);
 reset();failWorkspaceAllocation=true;call();bytes(false);rx();assert(writeCalls==0 && protectCalls==0);assert(properties.at("NRedImmediateSyncLastError")==KERN_RESOURCE_SHORTAGE);
 reset();call();bytes(true);rx();assert(writeCalls==N && properties.at("NRedImmediateSyncApplied")==1);
 int prior=writeCalls;call();assert(writeCalls==prior && properties.at("NRedImmediateSyncD07")==1);
 for(int p=1;p<=int(computeScratch?3:(legacyFlag?2:1));p++){reset();failProtect.insert(p);call();assert(writeCalls==0);bytes(false);rx();}
 for(int entry=1;entry<=int(N);entry++){reset();failWrite=entry;call();bytes(false);rx();assert(!properties.contains("NRedImmediateSyncApplied"));}
 reset();failProtect={int(computeScratch?3:(legacyFlag?2:1))+1,int(computeScratch?3:(legacyFlag?2:1))+2};call();bytes(false);rx();assert(properties.at("NRedImmediateSyncD13")==1 && !properties.contains("NRedImmediateSyncApplied"));
 reset();failProtect={int(computeScratch?3:(legacyFlag?2:1))+1};call();bytes(true);rx();assert(properties.at("NRedImmediateSyncApplied")==1 && properties.at("NRedImmediateSyncLastError")==42);
 // 每一页的最大权限恢复失败都必须撤回补丁，不能发布成功状态。
 const int pageCount=int(computeScratch?3:(legacyFlag?2:1));
 for(int page=0;page<pageCount;page++){
  reset();failProtect={pageCount+2+2*page};call();bytes(false);rx();
  assert(properties.at("NRedImmediateSyncD13")==1 && !properties.contains("NRedImmediateSyncApplied"));
 }
 // UUID 变化本身不再决定兼容；未知布局仍必须通过动态解析器的内容核验。
 reset();invalidCache=true;call();assert(protectCalls==0 && writeCalls==0);bytes(false);
 reset();code[1][0]^=1;call();assert(protectCalls==0 && writeCalls==0);
 for(const auto* other:{devicePatches->immediate,devicePatches->shared,devicePatches->blend,devicePatches->scratch}){
  if(other==active())continue;reset();size_t otherN=other==devicePatches->scratch?BaseN:(other==devicePatches->blend?3:2);
  for(size_t i=0;i<otherN;i++)memcpy(code[i].data(),other[i].patched,other[i].size);
  call();assert(protectCalls==0 && writeCalls==0);
  assert(properties.contains("NRedImmediateSyncD06")||properties.contains("NRedImmediateSyncD08"));
 }
 reset();memcpy(code[0].data(),active()[0].patched,active()[0].size);call();assert(protectCalls==0 && properties.at("NRedImmediateSyncD08")==1);
 for(int mode=1;mode<=4;mode++){reset();regionMode=mode;call();assert(protectCalls==0);}
 reset();noMap=true;call();assert(protectCalls==0);
 reset();originalResult=37;call();assert(protectCalls==0);
 reset();call(reinterpret_cast<void*>(217));assert(!properties.contains("NRedImmediateSyncWindowServerPID"));
 reset();isWindowServer=true;
 for(unsigned i=0;i<8;i++){
  for(size_t j=0;j<N;j++){auto& p=active()[j];memcpy(code[j].data(),p.original,p.size);}
  call(reinterpret_cast<void*>(uintptr_t(217+i)));bytes(true);rx();
 }
 assert(properties.at("NRedImmediateSyncWindowServerApplied")==4 && properties.at("NRedImmediateSyncWindowServerPID")==220);
 if(candidateFlag){assert(properties.at("NRedSrdSharedWindowServerPID")==220);}
 if(legacyFlag){assert(properties.at("NRedLegacyBlendWindowServerPID")==220 && properties.at("NRedLegacyBlendApplied")>0);}
 reset();isWindowServer=true;failWrite=1;call(reinterpret_cast<void*>(217));assert(!properties.contains("NRedImmediateSyncWindowServerPID"));
 if(computeScratch){
  reset();withdrawnCode=true;call();assert(protectCalls==0 && writeCalls==0);
  reset();isWeather=true;call(reinterpret_cast<void*>(1062));assert(properties.at("NRedComputeScratchWeatherPID")==1062 && properties.at("NRedComputeScratchApplied")==1);
  reset();isWeather=true;failWrite=4;call(reinterpret_cast<void*>(1062));assert(!properties.contains("NRedComputeScratchWeatherPID"));bytes(false);rx();
 }
 puts("PASS actual bridge: default-off/host/cache/code gates; every page/partial write rollback; RX/max protection restoration");
}
'''.replace("BODY", body).replace("INIT", init).replace("SUPPORTED_IDS", ",".join(map(str, sorted(devices)))).replace("properties.at(", "property(")
    file = WORK / "test-bridge.cpp"
    file.write_text(harness)
    exe = WORK / "test-bridge"
    subprocess.run(["clang++", "-std=c++23", "-O2", "-I", str(WORK / "stubs"), "-I", str(ROOT / "NootedRed"),
                    str(file), "-o", str(exe)], check=True, timeout=60)
    for device in sorted(devices):
        for mode in ["--immediate", "--candidate", "--legacy", "--scratch"]:
            subprocess.run([str(exe), mode, hex(device)], check=True, timeout=60)
    for args in [[], ["--transfer"]]:
        subprocess.run([str(exe),*args], check=True, timeout=60)


if __name__ == "__main__":
    main()
