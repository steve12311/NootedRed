"""编译真实 Metal 桥接，验证六种核显的动态 Scratch 及撤回策略拒绝。"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/dynamic-cache/compute'


def main():
    stub = WORK / 'stubs/IOKit'
    stub.mkdir(parents=True, exist_ok=True)
    (stub / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    util = WORK / 'stubs/Headers'
    util.mkdir(parents=True, exist_ok=True)
    (util / 'kern_util.hpp').write_text('#pragma once\n#include <IOKit/IOTypes.h>\n')
    source = (ROOT / 'NootedRed/UserSurfaceSync.cpp').read_text()
    if '--baseline' in sys.argv:
        source = subprocess.check_output(['git','show','6c7b0804b5b115052fab5914eecfad7090b53eb1:NootedRed/UserSurfaceSync.cpp'],cwd=ROOT,text=True)
    body = source.split('namespace {', 1)[1].split('} // namespace', 1)[0]
    template = (ROOT / 'tools/fixtures/vcn-user-bridge.cpp.in').read_text()
    preamble = template[:template.index('static bool isWindowServer')]
    preamble = preamble.replace('#include "UserVideoDecodeData.hpp"\n#include "UserVideoDecodeResolver.hpp"',
                               '#include <vector>\n#include "UserSurfaceSyncData.hpp"\n#include "UserSurfaceSyncResolver.hpp"')
    harness = preamble + r'''
#include "user-cache-dynamic.hpp"
static int proc_pid(proc_t);static void proc_name(int,char*,int);
static kern_return_t vm_protect(vm_map_t,uint64_t a,unsigned,int,int flags){
 protects++;if(protects==failedProtect || failedProtectCalls.contains(protects))return 42;assert(protections.contains(a));protections[a]=flags&7;return 0;}
BODY
static int original(proc_t,SharedRegionCheckArgs*,int* value){*value=123;return 0;}
static int proc_pid(proc_t){return 217;}static void proc_name(int,char* name,int size){snprintf(name,size,"Weather");}
static vm_map_t map(task_t){return reinterpret_cast<void*>(2);}
static kern_return_t query(vm_map_t,uint64_t* a,uint64_t* size,unsigned*,int* p,unsigned* count){
 assert(*count==VM_REGION_SUBMAP_SHORT_INFO_COUNT_64);auto*i=reinterpret_cast<vm_region_submap_short_info_data_64_t*>(p);
 i->protection=protections.at(*a);i->is_submap=0;*size=4096;return 0;}
static uint64_t initAddress,overrideAddress,constructorAddress,settingsAddress,dumpAddress;
static void setup(unsigned id){
 using namespace UserSurfaceSyncData;
 cache(ScratchResolverData::ResolverImages,ScratchResolverData::ResolverFunctions);
 devicePatches=findDevice(id);assert(devicePatches);computeScratch=legacyBlend=srdShared=true;
 auto image=imageAddresses[0];initAddress=image+0x21f80;overrideAddress=image+0x23ff8;constructorAddress=image+0x28fe5;
 settingsAddress=image+0x26000;dumpAddress=image+0x27000;
 put(initAddress,Original0,sizeof(Original0));put(overrideAddress,Original1,sizeof(Original1));
 put(constructorAddress,BlendConstructor,sizeof(BlendConstructor));put(settingsAddress,NativeOverride,sizeof(NativeOverride));
 put(dumpAddress,NativeDump,sizeof(NativeDump));word32(image+0x71064,0x40400000);
 uint64_t targets[]={image+0x71064,settingsAddress,dumpAddress};
 for(auto link:OriginalInitLinks)rel(initAddress,link.offset,targets[link.target]);rel(overrideAddress,6,settingsAddress);
 seenStages=appliedCount=errorCount=windowServerCount=weatherCount=0;
 originalCheck=reinterpret_cast<uint64_t>(original);readUser=reader;writeUser=writer;getTaskMap=map;queryRegion=query;
}
static void call(){SharedRegionCheckArgs args{0x1000};int value=0;assert(wrappedSharedRegionCheck(reinterpret_cast<void*>(217),&args,&value)==0&&value==123);}
int main(){
 unsigned cases=0;
 for(auto device:UserSurfaceSyncData::DevicePatches){
  setup(device.deviceID);UserSurfaceSyncResolver::Plan plan;
  assert(UserSurfaceSyncResolver::resolve(reader,base,device,true,true,plan,true));
  assert(plan.patches[3].offset+base==functionAddresses[0]);
  pages(plan.patches,4);assert(protections.size()==8);auto initial=blocks;
  call();if(!properties.contains("NRedImmediateSyncDynamicApplied")){for(auto[k,v]:properties)fprintf(stderr,"%s=%u\n",k.c_str(),v);}assert(properties.contains("NRedImmediateSyncDynamicApplied")&&properties.at("NRedComputeScratchWeatherApplied")==1);rx();assert(writes==4);
  for(unsigned i=0;i<4;i++){uint8_t b[UserSurfaceSyncData::MaxPatchSize];assert(reader(base+plan.patches[i].offset,b,plan.patches[i].size)==0);assert(memcmp(b,plan.patches[i].patched,plan.patches[i].size)==0);}
  auto before=writes;call();assert(writes==before&&properties.at("NRedImmediateSyncD07")==1);cases++;
  for(unsigned entry=1;entry<=4;entry++){
   setup(device.deviceID);pages(plan.patches,4);initial=blocks;failWrite=entry;call();assert(blocks==initial);rx();assert(!properties.contains("NRedImmediateSyncDynamicApplied"));cases++;
  }
  for(unsigned page=1;page<=8;page++){
   setup(device.deviceID);pages(plan.patches,4);initial=blocks;failedProtect=page;call();assert(blocks==initial&&writes==0);rx();cases++;
  }
  setup(device.deviceID);pages(plan.patches,4);initial=blocks;failedProtectCalls={9,10};call();assert(blocks==initial);rx();cases++;
 }
 for(unsigned kind=0;kind<9;kind++){
  setup(0x15e7);using namespace UserSurfaceSyncData::ScratchResolverData;
  switch(kind){
   case 0:word32(functionAddresses[0]+100,0);break;
   case 1:word32(functionAddresses[1]+100,0);break;
   case 2:word64(base+0x7000000,0);break;
   case 3:rel(functionAddresses[0],ResolverFunctions[0].originalRefs[0].offset,base+0x7100000);break;
   case 4:word32(base+452,8193);break;
   case 5:{std::vector<uint8_t>b(ResolverFunctions[0].size);assert(reader(functionAddresses[0],b.data(),b.size())==0);put(imageAddresses[0]+0x60000,b.data(),b.size());}break;
   case 6:word32(constructorAddress+UserSurfaceSyncData::BlendGateInConstructor,0);break;
   case 7:word32(base+0x3000,0);break;
   case 8:failedRead=functionAddresses[0];break;
  }
  auto initial=blocks;call();assert(blocks==initial&&writes==0&&protects==0);cases++;
 }
 // 两处已撤回 VT 跳转，逐一破坏原生完整函数中的原始分支。
 for(auto guard:UserSurfaceSyncData::WithdrawnTransferGuards){
  setup(0x15e7);const auto& fn=UserSurfaceSyncData::ScratchResolverData::ResolverFunctions[1];
  auto address=functionAddresses[1]+UserSurfaceSyncData::CacheBase+guard.offset-
    (0x7ff81524f000ULL+fn.rva);
  uint8_t b=0x90;put(address,&b,1);auto initial=blocks;call();assert(blocks==initial&&writes==0&&protects==0);cases++;
 }
 printf("PASS %u compute dynamic bridge cases: six devices; relocated scratch/GOT/VT; eight-page rollback; withdrawn strategy guards\n",cases);
}
'''.replace('BODY', body)
    if '--baseline' in sys.argv:
        harness = harness.replace('call();if(!properties.contains("NRedImmediateSyncDynamicApplied"))', 'call();assert(writes==0&&protects==0);puts("PASS baseline reproduces unknown-cache rejection");return 0;if(!properties.contains("NRedImmediateSyncDynamicApplied"))',1)
    file, exe = WORK / 'test.cpp', WORK / 'test'
    file.write_text(harness)
    flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if '--sanitize' in sys.argv else []
    subprocess.run(['clang++', '-std=c++23', '-O2', *flags, '-I'+str(WORK/'stubs'),
                    '-I'+str(ROOT/'NootedRed'), '-I'+str(ROOT/'tools/fixtures'), str(file), '-o', str(exe)], check=True, timeout=60)
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
